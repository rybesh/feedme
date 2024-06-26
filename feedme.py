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
from base64 import b64encode
from datetime import datetime, timezone, timedelta
from feedgen.feed import FeedGenerator
from ratelimit import limits, sleep_and_retry
from tendo.singleton import SingleInstance, SingleInstanceException
from typing import Optional, NamedTuple, Iterator, Any, TypeAlias, cast
from urllib.parse import urlparse, parse_qs, quote
from json.decoder import JSONDecodeError

from config import config

ITEMS_PAGE_SIZE = 200

PriceSuggestions: TypeAlias = dict[str, float]


class Listing(NamedTuple):
    id: str
    url: str
    title: str
    start_time: datetime
    age_in_days: int
    image_url: str
    price: float
    shipping_price: float | None
    country: str
    buy_it_now: bool
    search_params: dict[str, str | dict[str, str]]
    price_suggestions: PriceSuggestions
    release_id: int | None


class APIException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class TooManyAPICallsException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class BadSearchURLException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class TimeLimitException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


def now() -> datetime:
    return datetime.now(timezone.utc)


def log(x: Any) -> None:
    print(x, file=sys.stderr)


bearer_token = None


def refresh_bearer_token(client: httpx.Client) -> None:
    token = b64encode(f"{config.APP_ID}:{config.CERT_ID}\n".encode()).decode()
    r = client.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {token}",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
    )
    o = r.json()
    global bearer_token
    bearer_token = o["access_token"]


@sleep_and_retry
@limits(calls=1, period=1)
def call_api(
    client: httpx.Client,
    search_params: dict[str, str | dict[str, str]],
) -> dict:
    query_params = {}
    for key, value in search_params.items():
        if isinstance(value, dict):
            query_params[key] = ",".join([f"{k}:{v}" for k, v in value.items()])
        else:
            query_params[key] = value

    if bearer_token is None:
        refresh_bearer_token(client)

    tries = 0
    while True:
        try:
            r = client.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                params=query_params,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )

            call_api.counter += 1

            if not r.status_code == 200:
                message_parts = [f"GET {r.url} failed ({r.status_code})"]
                try:
                    for e in r.json().get("errors", []):
                        message_parts.append(e["message"])
                        if e["category"] == "REQUEST" and e["errorId"] == 2001:
                            raise TooManyAPICallsException(
                                f"Too many API calls ({call_api.counter}) within 24 hours"
                            )
                except (KeyError, JSONDecodeError):
                    pass
                raise APIException("\n".join(message_parts))

            o = r.json()
            for w in o.get("warnings", []):
                log(f"{w['category']} ({w['errorId']}) {w['message']}")

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
        d["category_ids"] = parts[3]
    else:
        raise BadSearchURLException(f"Cannot handle path:\n{path}")


def add_keywords(params: dict[str, list[str]], d: dict[str, str]):
    if "_nkw" in params:
        d["q"] = params["_nkw"][0]


def add_location_preference(
    params: dict[str, list[str]], d: dict[str, str | dict[str, str]]
):
    if "LH_PrefLoc" in params:
        filters: dict[str, str] = cast(dict[str, str], d.get("filter", {}))
        if params["LH_PrefLoc"][0] == "1":
            filters["itemLocationCountry"] = "US"
        elif params["LH_PrefLoc"][0] == "2":
            filters["itemLocationRegion"] = "WORLDWIDE"
        elif params["LH_PrefLoc"][0] == "3":
            # not possible to narrow to North America
            filters["itemLocationCountry"] = "US"
        else:
            raise BadSearchURLException(
                f"Cannot handle location preference:\n{params['LH_PrefLoc'][0]}"
            )
        d["filters"] = filters


def parse_search_params(url: str) -> dict[str, str | dict[str, str]]:
    d = {
        "filter": {"buyingOptions": "{AUCTION|FIXED_PRICE}"},
        "limit": f"{ITEMS_PAGE_SIZE}",
        "sort": "newlyListed",
    }
    o = urlparse(url)
    params = parse_qs(o.query)
    if o.path.endswith("i.html"):
        add_category(o.path, d)
        add_keywords(params, d)
        add_location_preference(params, d)
    else:
        raise BadSearchURLException(f"Cannot handle url:\n{url}")
    return d


def get_results(
    client: httpx.Client, search_url: str, last_updated: datetime
) -> Iterator[tuple[dict, dict[str, str | dict[str, str]]]]:
    search_params = parse_search_params(search_url)
    filters: dict[str, str] = cast(dict[str, str], search_params.get("filter", {}))
    filters["itemStartDate"] = f"[{last_updated.isoformat().replace('+00:00', 'Z')}]"
    search_params["filter"] = filters
    while True:
        response = call_api(client, search_params)
        for item in response.get("itemSummaries", []):
            yield item, search_params.copy()
        if "next" in response:
            search_params["offset"] = str(response["offset"] + ITEMS_PAGE_SIZE)
        else:
            break


def item_to_listing(
    item: dict,
    search_params: dict[str, str | dict[str, str]],
    price_suggestions: PriceSuggestions,
    release_id: int | None,
) -> Listing:
    # import pprint

    # pprint.pprint(item)

    start_time = datetime.fromisoformat(item["itemCreationDate"].replace("Z", "+00:00"))

    price = float(item["price" if "price" in item else "currentBidPrice"]["value"])

    shipping_price = None
    for shipping_option in item.get("shippingOptions", []):
        if shipping_option.get("shippingCostType") == "FIXED":
            shipping_cost = shipping_option.get("shippingCost", {})
            if "value" in shipping_cost:
                shipping_price = float(shipping_cost["value"])

    return Listing(
        quote(item["itemId"]),
        item["itemWebUrl"],
        item["title"],
        start_time,
        (now() - start_time).days,
        item["image"]["imageUrl"],
        price,
        shipping_price,
        item["itemLocation"]["country"],
        "FIXED_PRICE" in item["buyingOptions"],
        search_params,
        price_suggestions,
        release_id,
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
    client: httpx.Client,
    search_urls: list[tuple[str, PriceSuggestions, int | None]],
    last_updated: datetime,
    minutes: int | None = None,
) -> Iterator[Listing]:
    last_url = None
    next_url = load_next_url()

    if next_url is None:
        log(f"Beginning listings search with {len(search_urls)} urls")

    try:
        start = time.time()

        for i, (url, price_suggestions, release_id) in enumerate(search_urls, start=1):
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
                    yield item_to_listing(
                        item, search_params, price_suggestions, release_id
                    )
            except (BadSearchURLException, APIException) as e:
                log(e)

            elapsed = time.time() - start
            if minutes and ((elapsed / 60) > minutes):
                raise TimeLimitException(f"Elapsed time exceeded {minutes} minutes")

        log("Completed listings search")
        clear_next_url()
        last_url = None

    except (TooManyAPICallsException, TimeLimitException) as e:
        log(e)
    finally:
        if last_url is not None:
            save_next_url(last_url)


def describe(listing: Listing) -> str:
    description = (
        f"<p>${listing.price:.2f}{' (BIN)' if listing.buy_it_now else ''}</p>"
        f"<p>ships from {listing.country}</p>"
    )
    if listing.shipping_price is not None:
        description += f"<p>shipping: ${listing.shipping_price:.2f}</p>"
    
    for condition in ("VGP", "NM"):
        if condition in listing.price_suggestions:
            description += f"<p>suggested price ({condition}): ${listing.price_suggestions[condition]:.2f}</p>"
    
    description += f'<img src="{listing.image_url}"/>'

    if "q" in listing.search_params:
        description += f"<p>{html.escape(str(listing.search_params['q']))}</p>"

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
        and listing.age_in_days <= config.MAX_LISTING_AGE_DAYS
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-m",
        "--minutes",
        type=int,
        help="number of minutes to run before exiting",
    )
    parser.add_argument(
        "searches", help="text file with manually edited eBay search URLs"
    )
    parser.add_argument(
        "pickle", help="pickle file with autogenerated eBay search URLs"
    )
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

    with open(args.pickle, "rb") as f:
        search_urls = pickle.load(f)

    with open(args.searches) as f:
        for url in [line.strip() for line in f]:
            search_urls.append((url, {}, None))

    with httpx.Client() as client:
        for listing in get_listings(
            client, search_urls, last_updated, minutes=args.minutes
        ):
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
