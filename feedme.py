#! ./venv/bin/python3

import argparse
import atoma
import html
import httpx
import os
import re
import sys
import time
import pickle

from atoma.atom import AtomEntry, AtomFeed
from datetime import datetime, timezone, timedelta
from feedgen.feed import FeedGenerator
from ratelimit import limits, sleep_and_retry
from tendo.singleton import SingleInstance, SingleInstanceException
from typing import Optional, NamedTuple, Iterator, Any
from urllib.parse import urlparse, parse_qs
from json.decoder import JSONDecodeError

from config import config

headers = {
    "OPERATION-NAME": "findItemsAdvanced",
    "SERVICE-VERSION": "1.13.0",
    "SECURITY-APPNAME": config.APP_ID,
    "RESPONSE-DATA-FORMAT": "JSON",
    "REST-PAYLOAD": "",
}


class Listing(NamedTuple):
    id: str
    url: str
    title: str
    start_time: datetime
    age_in_days: int
    active: bool
    image_url: str
    price: float
    buy_it_now: bool
    search_params: dict[str, str]


class APIException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class TooManyAPICallsException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class BadSearchURLException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


def now() -> datetime:
    return datetime.now(timezone.utc)


def log(x: Any) -> None:
    print(x, file=sys.stderr)


@sleep_and_retry
@limits(calls=1, period=1)
def call_api(client: httpx.Client, search_params: dict[str, str]) -> dict:
    tries = 0
    while True:
        try:
            r = client.get(
                "https://svcs.ebay.com/services/search/FindingService/v1",
                params=(headers | search_params),
            )

            call_api.counter += 1

            if not r.status_code == 200:
                message_parts = [f"GET {r.url} failed ({r.status_code})"]
                try:
                    upstream_message = r.json()["errorMessage"][0]["error"][0][
                        "message"
                    ][0]
                    if (
                        upstream_message
                        == "Service call has exceeded the number of times the operation is allowed to be called"
                    ):
                        raise TooManyAPICallsException(
                            f"Too many API calls ({call_api.counter}) within 24 hours"
                        )
                    else:
                        message_parts.append(upstream_message)
                except (KeyError, JSONDecodeError):
                    pass
                raise APIException("\n".join(message_parts))

            o = r.json()["findItemsAdvancedResponse"][0]
            if not o["ack"][0] == "Success":
                raise APIException(
                    f"GET {r.url}:\nfindItemsAdvancedResponse ack was {o['ack'][0]}"
                )
            return o
        except httpx.RequestError as e:
            tries += 1
            if tries > 10:
                raise APIException(f"API call failed ({e})") from e
            else:
                time.sleep(60)


call_api.counter = 0


def add_category(path: str, d: dict[str, str]):
    parts = path.split("/")
    if len(parts) == 3:
        pass
    elif len(parts) == 5:
        d["categoryId"] = parts[3]
    else:
        raise BadSearchURLException(f"Cannot handle path:\n{path}")


def add_keywords(params: dict[str, list[str]], d: dict[str, str]):
    if "_nkw" in params:
        d["keywords"] = params["_nkw"][0]
    if "LH_TitleDesc" in params and params["LH_TitleDesc"] == "1":
        d["descriptionSearch"] = "true"


def add_location_preference(params: dict[str, list[str]], d: dict[str, str]):
    if "LH_PrefLoc" in params:
        d["itemFilter.name"] = "LocatedIn"
        if params["LH_PrefLoc"][0] == "1":
            d["itemFilter.value"] = "US"
        elif params["LH_PrefLoc"][0] == "2":
            d["itemFilter.value"] = "WorldWide"
        elif params["LH_PrefLoc"][0] == "3":
            d["itemFilter.value"] = "North America"
        else:
            raise BadSearchURLException(
                f"Cannot handle location preference:\n{params['LH_PrefLoc'][0]}"
            )


def add_seller(params: dict[str, list[str]], d: dict[str, str]):
    if "_ssn" in params:
        d["itemFilter.name"] = "Seller"
        d["itemFilter.value"] = params["_ssn"][0]


def add_mod_time_from(last_updated: datetime, d: dict[str, str]):
    mod_time_from = last_updated.isoformat().replace("+00:00", "Z")
    if "itemFilter.name" in d:
        d["itemFilter(0).name"] = d.pop("itemFilter.name")
        d["itemFilter(0).value"] = d.pop("itemFilter.value")
        d["itemFilter(1).name"] = "ModTimeFrom"
        d["itemFilter(1).value"] = mod_time_from
    else:
        d["itemFilter.name"] = "ModTimeFrom"
        d["itemFilter.value"] = mod_time_from


def parse_search_params(url: str) -> dict[str, str]:
    d = {
        "sortOrder": "StartTimeNewest",
        "outputSelector": "PictureURLSuperSize",
        "paginationInput.entriesPerPage": "100",
    }
    o = urlparse(url)
    params = parse_qs(o.query)
    if o.path.endswith("i.html"):
        add_category(o.path, d)
        add_keywords(params, d)
        add_location_preference(params, d)
    elif o.path.endswith("m.html"):
        add_seller(params, d)
    else:
        raise BadSearchURLException(f"Cannot handle url:\n{url}")
    return d


def get_next_page(response: dict) -> Optional[int]:
    page_number = int(response["paginationOutput"][0]["pageNumber"][0])
    total_pages = int(response["paginationOutput"][0]["totalPages"][0])
    if page_number < total_pages:
        return page_number + 1
    else:
        return None


def get_results(
    client: httpx.Client, search_url: str, last_updated: datetime
) -> Iterator[tuple[dict, dict[str, str]]]:
    search_params = parse_search_params(search_url)
    add_mod_time_from(last_updated, search_params)
    while True:
        response = call_api(client, search_params)
        for item in response["searchResult"][0].get("item", []):
            yield item, search_params.copy()
        next_page = get_next_page(response)
        if next_page is None:
            break
        else:
            search_params["paginationInput.pageNumber"] = str(next_page)


def item_to_listing(item: dict, search_params: dict[str, str]) -> Listing:
    start_time = datetime.fromisoformat(
        item["listingInfo"][0]["startTime"][0].replace("Z", "+00:00")
    )
    return Listing(
        item["itemId"][0],
        item["viewItemURL"][0],
        item["title"][0],
        start_time,
        (now() - start_time).days,
        item["sellingStatus"][0]["sellingState"][0] == "Active",
        item.get("pictureURLSuperSize", item["galleryURL"])[0],
        float(item["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"]),
        item["listingInfo"][0]["listingType"][0] in ("AuctionWithBIN", "FixedPrice"),
        search_params,
    )


NEXT_URL_PICKLE = "next-url.pickle"


def load_next_url() -> str | None:
    try:
        with open(NEXT_URL_PICKLE, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None


def save_next_url(next_url: str) -> None:
    with open(NEXT_URL_PICKLE, "wb") as f:
        pickle.dump(next_url, f, pickle.HIGHEST_PROTOCOL)


def clear_next_url() -> None:
    try:
        os.remove(NEXT_URL_PICKLE)
    except OSError:
        pass


def get_listings(
    client: httpx.Client, search_urls: list[str], last_updated: datetime
) -> Iterator[Listing]:
    last_url = None
    next_url = load_next_url()

    if next_url is None:
        log(f"Beginning listings search with {len(search_urls)} urls")

    try:
        for i, url in enumerate(search_urls, start=1):
            last_url = url

            if next_url is not None:
                if url == next_url:
                    next_url = None
                    log(f"Resuming listings search with url #{i} of {len(search_urls)}")
                else:
                    # skip until we get to next_url
                    continue

            try:
                for item, search_params in get_results(client, url, last_updated):
                    yield item_to_listing(item, search_params)
            except (BadSearchURLException, APIException) as e:
                log(e)

        log("Completed listings search")
        clear_next_url()

    except TooManyAPICallsException as e:
        log(e)
        if last_url is not None:
            save_next_url(last_url)


def describe(listing: Listing) -> str:
    description = (
        f"<p>${listing.price:.2f}{' (BIN)' if listing.buy_it_now else ''}</p>"
        f'<img src="{listing.image_url}"/>'
    )
    if "keywords" in listing.search_params:
        description += f"<p>{html.escape(listing.search_params['keywords'])}</p>"
    if (
        "itemFilter.name" in listing.search_params
        and listing.search_params["itemFilter.name"] == "Seller"
    ):
        description += (
            f"<p>{html.escape(listing.search_params['itemFilter.value'])}</p>"
        )
    elif (
        "itemFilter(0).name" in listing.search_params
        and listing.search_params["itemFilter(0).name"] == "Seller"
    ):
        description += (
            f"<p>{html.escape(listing.search_params['itemFilter(0).value'])}</p>"
        )
    return description


def copy_entry(entry: AtomEntry, fg: FeedGenerator) -> None:
    fe = fg.add_entry(order="append")
    fe.id(entry.id_)
    fe.title(entry.title.value)
    fe.updated((entry.updated or now()).isoformat())
    fe.link(href=entry.links[0].href)
    if entry.content:
        fe.content(entry.content.value, type="html")


tag_uri = re.compile(r"tag:feedme.aeshin.org,2022:item-(\d+)")


def parse_listing_id(entry_id: str) -> Optional[str]:
    m = tag_uri.match(entry_id)
    return m.group(1) if m else None


def copy_remaining_entries(
    feed: Optional[AtomFeed], fg: FeedGenerator, entry_count: int, listing_ids: set[str]
) -> None:
    if feed is not None:
        for entry in feed.entries:
            listing_id = parse_listing_id(entry.id_)
            if entry_count < config.MAX_FEED_ENTRIES:
                if listing_id and listing_id not in listing_ids:
                    copy_entry(entry, fg)
                    entry_count += 1
            else:
                break


def include_in_feed(listing: Listing, listing_ids: set[str]) -> bool:
    return (
        listing.id not in listing_ids
        and listing.active
        and listing.age_in_days <= config.MAX_LISTING_AGE_DAYS
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("searches", help="file with eBay search URLs")
    parser.add_argument("feed", help="Atom feed file to create or update")
    args = parser.parse_args()

    existing_feed = None
    entry_count = 0
    listing_ids = set()
    last_updated = now() - timedelta(days=1)

    if os.path.exists(args.feed):
        existing_feed = atoma.parse_atom_file(args.feed)
        for entry in existing_feed.entries:
            if entry.updated is not None and entry.updated > last_updated:
                last_updated = entry.updated

    fg = FeedGenerator()
    fg.id(config.FEED_URL)
    fg.title("eBay Searches")
    fg.updated(now())
    fg.link(href=config.FEED_URL, rel="self")
    fg.author({"name": config.FEED_AUTHOR_NAME, "email": config.FEED_AUTHOR_EMAIL})

    with open(args.searches) as f:
        search_urls = [line.strip() for line in f]

    with httpx.Client() as client:
        for listing in get_listings(client, search_urls, last_updated):
            if include_in_feed(listing, listing_ids):
                listing_ids.add(listing.id)
                fe = fg.add_entry(order="append")
                fe.id(f"tag:feedme.aeshin.org,2022:item-{listing.id}")
                fe.title(listing.title)
                fe.updated(listing.start_time.isoformat())
                fe.link(href=listing.url)
                fe.content(describe(listing), type="html")

                entry_count += 1
                if entry_count > config.MAX_FEED_ENTRIES:
                    break

    log(f"Added {entry_count} items to feed")

    copy_remaining_entries(existing_feed, fg, entry_count, listing_ids)
    fg.atom_file(f"{args.feed}.new", pretty=True)
    os.rename(f"{args.feed}.new", args.feed)


if __name__ == "__main__":
    try:
        me = SingleInstance()
        main()
    except SingleInstanceException as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        sys.exit(0)
