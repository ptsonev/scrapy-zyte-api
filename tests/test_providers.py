import sys

import pytest

pytest.importorskip("scrapy_poet")

import asyncio

import attrs
from pytest_twisted import ensureDeferred
from scrapy import Request, Spider
from scrapy_poet import DummyResponse
from scrapy_poet.injection import Injector
from scrapy_poet.utils.testing import HtmlResource, crawl_single_item
from scrapy_poet.utils.testing import create_scrapy_settings as _create_scrapy_settings
from twisted.internet import defer, reactor
from twisted.web.client import Agent, readBody
from web_poet import (
    AnyResponse,
    BrowserHtml,
    BrowserResponse,
    HttpResponse,
    ItemPage,
    field,
    handle_urls,
)
from zyte_common_items import BasePage, Product

from scrapy_zyte_api._annotations import ExtractFrom
from scrapy_zyte_api.handler import ScrapyZyteAPIDownloadHandler
from scrapy_zyte_api.providers import ZyteApiProvider

from . import SETTINGS, get_crawler
from .mockserver import get_ephemeral_port


def create_scrapy_settings():
    settings = _create_scrapy_settings(None)
    for setting, value in SETTINGS.items():
        if setting.endswith("_MIDDLEWARES") and settings[setting]:
            settings[setting].update(value)
        else:
            settings[setting] = value
    return settings


@attrs.define
class ProductPage(BasePage):
    html: BrowserHtml
    response: BrowserResponse
    product: Product


class ZyteAPISpider(Spider):
    url: str

    def start_requests(self):
        yield Request(self.url, callback=self.parse_)

    def parse_(self, response: DummyResponse, page: ProductPage):
        yield {
            "html": page.html,
            "response_html": page.response.html,
            "product": page.product,
        }


@ensureDeferred
async def test_provider(mockserver):
    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 0}
    item, url, _ = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    assert item["html"] == "<html><body>Hello<h1>World!</h1></body></html>"
    assert item["response_html"] == "<html><body>Hello<h1>World!</h1></body></html>"
    assert item["product"] == Product.from_dict(
        dict(
            url=url,
            name="Product name",
            price="10",
            currency="USD",
        )
    )


@attrs.define
class MyItem:
    url: str


@attrs.define
class MyPage(ItemPage[MyItem]):
    response: BrowserResponse

    @field
    def url(self) -> str:
        return str(self.response.url)


@ensureDeferred
async def test_itemprovider_requests_direct_dependencies(fresh_mockserver):
    class ItemDepSpider(ZyteAPISpider):
        def parse_(  # type: ignore[override]
            self,
            response: DummyResponse,
            browser_response: BrowserResponse,
            product: Product,
        ):
            yield {
                "product": product,
                "browser_response": browser_response,
            }

    port = get_ephemeral_port()
    handle_urls(f"{fresh_mockserver.host}:{port}")(MyPage)

    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = fresh_mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 1100}
    item, url, _ = await crawl_single_item(
        ItemDepSpider, HtmlResource, settings, port=port
    )
    count_resp = await Agent(reactor).request(
        b"GET", fresh_mockserver.urljoin("/count").encode()
    )
    call_count = int((await readBody(count_resp)).decode())
    assert call_count == 1
    assert "browser_response" in item
    assert "product" in item


@ensureDeferred
async def test_itemprovider_requests_indirect_dependencies(fresh_mockserver):
    class ItemDepSpider(ZyteAPISpider):
        def parse_(self, response: DummyResponse, product: Product, my_item: MyItem):  # type: ignore[override]
            yield {
                "product": product,
                "my_item": my_item,
            }

    port = get_ephemeral_port()
    handle_urls(f"{fresh_mockserver.host}:{port}")(MyPage)

    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = fresh_mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 1100}
    item, url, _ = await crawl_single_item(
        ItemDepSpider, HtmlResource, settings, port=port
    )
    count_resp = await Agent(reactor).request(
        b"GET", fresh_mockserver.urljoin("/count").encode()
    )
    call_count = int((await readBody(count_resp)).decode())
    assert call_count == 1
    assert "my_item" in item
    assert "product" in item


@ensureDeferred
async def test_itemprovider_requests_indirect_dependencies_workaround(fresh_mockserver):
    class ItemDepSpider(ZyteAPISpider):
        def parse_(self, response: DummyResponse, product: Product, browser_response: BrowserResponse, my_item: MyItem):  # type: ignore[override]
            yield {
                "product": product,
                "my_item": my_item,
                "browser_response": browser_response,
            }

    port = get_ephemeral_port()
    handle_urls(f"{fresh_mockserver.host}:{port}")(MyPage)

    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = fresh_mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 1}
    item, url, _ = await crawl_single_item(
        ItemDepSpider, HtmlResource, settings, port=port
    )
    count_resp = await Agent(reactor).request(
        b"GET", fresh_mockserver.urljoin("/count").encode()
    )
    call_count = int((await readBody(count_resp)).decode())
    assert call_count == 1
    assert "my_item" in item
    assert "product" in item
    assert "browser_response" in item


@ensureDeferred
async def test_provider_params(mockserver):
    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 0}
    settings["ZYTE_API_PROVIDER_PARAMS"] = {"geolocation": "IE"}
    _, _, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    assert crawler.stats.get_value("scrapy-zyte-api/request_args/browserHtml") == 1
    assert crawler.stats.get_value("scrapy-zyte-api/request_args/geolocation") == 1


@ensureDeferred
async def test_provider_params_remove_unused_options(mockserver):
    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 0}
    settings["ZYTE_API_PROVIDER_PARAMS"] = {
        "productOptions": {"extractFrom": "httpResponseBody"},
        "productNavigationOptions": {"extractFrom": "httpResponseBody"},
    }
    _, _, crawler = await crawl_single_item(ZyteAPISpider, Product, settings)
    assert crawler.stats.get_value("scrapy-zyte-api/request_args/product") == 1
    assert crawler.stats.get_value("scrapy-zyte-api/request_args/productOptions") == 1
    assert (
        crawler.stats.get_value("scrapy-zyte-api/request_args/productNavigationOptions")
        is None
    )


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="No Annotated support in Python < 3.9"
)
@ensureDeferred
async def test_provider_extractfrom(mockserver):
    from typing import Annotated

    @attrs.define
    class AnnotatedProductPage(BasePage):
        product: Annotated[Product, ExtractFrom.httpResponseBody]
        product2: Annotated[Product, ExtractFrom.httpResponseBody]

    class AnnotatedZyteAPISpider(ZyteAPISpider):
        def parse_(self, response: DummyResponse, page: AnnotatedProductPage):  # type: ignore[override]
            yield {
                "product": page.product,
                "product2": page.product,
            }

    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 0}

    item, url, _ = await crawl_single_item(
        AnnotatedZyteAPISpider, HtmlResource, settings
    )
    assert item["product"] == Product.from_dict(
        dict(
            url=url,
            name="Product name (from httpResponseBody)",
            price="10",
            currency="USD",
        )
    )


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="No Annotated support in Python < 3.9"
)
@ensureDeferred
async def test_provider_extractfrom_double(mockserver, caplog):
    from typing import Annotated

    @attrs.define
    class AnnotatedProductPage(BasePage):
        product: Annotated[Product, ExtractFrom.httpResponseBody]
        product2: Annotated[Product, ExtractFrom.browserHtml]

    class AnnotatedZyteAPISpider(ZyteAPISpider):
        def parse_(self, response: DummyResponse, page: AnnotatedProductPage):  # type: ignore[override]
            yield {
                "product": page.product,
            }

    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = mockserver.urljoin("/")
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 0}

    item, _, _ = await crawl_single_item(AnnotatedZyteAPISpider, HtmlResource, settings)
    assert item is None
    assert "Multiple different extractFrom specified for product" in caplog.text


@defer.inlineCallbacks
def run_provider(server, to_provide, settings_dict=None, request_meta=None):
    class AnyResponseSpider(Spider):
        name = "any_response"

    request = Request(server.urljoin("/some-page"), meta=request_meta)
    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = server.urljoin("/")
    if settings_dict:
        settings.update(settings_dict)
    crawler = get_crawler(settings, AnyResponseSpider)
    yield from crawler.engine.open_spider(crawler.spider)
    injector = Injector(crawler)
    provider = ZyteApiProvider(injector)

    coro = provider(to_provide, request, crawler)
    results = yield defer.Deferred.fromFuture(asyncio.ensure_future(coro))

    return results


@defer.inlineCallbacks
def test_provider_any_response(mockserver):
    # Use only one instance of the mockserver for faster tests.
    def provide(*args, **kwargs):
        return run_provider(mockserver, *args, **kwargs)

    results = yield provide(set())
    assert results == []

    # AnyResponse would use HttpResponse by default, if neither BrowserResponse
    # nor HttpResponse is available.
    results = yield provide(
        {
            AnyResponse,
        }
    )
    assert len(results) == 1
    assert type(results[0]) == AnyResponse
    assert type(results[0].response) == HttpResponse

    # Same case as above, HttpResopnse is used by default
    results = yield provide({AnyResponse, Product})
    assert len(results) == 2
    assert type(results[0]) == AnyResponse
    assert type(results[1]) == Product

    # AnyResponse should re-use BrowserResponse if available.
    results = yield provide({AnyResponse, BrowserResponse})
    assert len(results) == 2
    assert type(results[0]) == BrowserResponse
    assert type(results[1]) == AnyResponse
    assert id(results[0]) == id(results[1].response)

    # AnyResponse should re-use BrowserHtml if available.
    results = yield provide({AnyResponse, BrowserHtml})
    assert len(results) == 2
    assert type(results[0]) == BrowserHtml
    assert type(results[1]) == AnyResponse
    # diff instance due to casting
    assert results[0] == results[1].response.html  # type: ignore[union-attr]

    results = yield provide({AnyResponse, BrowserResponse, BrowserHtml})
    assert len(results) == 3
    assert type(results[0]) == BrowserHtml
    assert type(results[1]) == BrowserResponse
    assert type(results[2]) == AnyResponse
    assert results[0] == results[1].html
    assert results[0] == results[2].response.html  # type: ignore[union-attr]

    # NOTES: This is hard to test in this setup and would result in being empty.
    # This will be tested in a spider-setup instead so that HttpResponseProvider
    # can participate.
    # results = yield provide({AnyResponse, HttpResponse})
    # assert results == []

    # For the following cases, extraction source isn't available in `to_provided`
    # but are in the `*.extractFrom` parameter.

    settings_dict = {
        "ZYTE_API_PROVIDER_PARAMS": {"productOptions": {"extractFrom": "browserHtml"}}
    }

    results = yield provide({AnyResponse, Product}, settings_dict)
    assert len(results) == 2
    assert type(results[0]) == AnyResponse
    assert type(results[0].response) == BrowserResponse
    assert type(results[1]) == Product

    results = yield provide({AnyResponse, BrowserHtml, Product}, settings_dict)
    assert len(results) == 3
    assert type(results[0]) == BrowserHtml
    assert type(results[1]) == AnyResponse
    assert type(results[1].response) == BrowserResponse
    assert type(results[2]) == Product
    assert results[0] == results[1].response.html  # diff instance due to casting

    settings_dict = {
        "ZYTE_API_PROVIDER_PARAMS": {
            "productOptions": {"extractFrom": "httpResponseBody"}
        }
    }
    request_meta = {"zyte_api": {"httpResponseBody": True, "httpResponseHeaders": True}}

    results = yield provide({AnyResponse, Product}, settings_dict, request_meta)
    assert len(results) == 2
    assert type(results[0]) == AnyResponse
    assert type(results[0].response) == HttpResponse
    assert type(results[1]) == Product


class RecordingHandler(ScrapyZyteAPIDownloadHandler):
    """Subclasses the original handler in order to record the Zyte API parameters
    used for each downloading request.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params = []

    def _log_request(self, params):
        self.params.append(params)


def provider_settings(server):
    settings = create_scrapy_settings()
    settings["ZYTE_API_URL"] = server.urljoin("/")
    settings["ZYTE_API_TRANSPARENT_MODE"] = True
    settings["SCRAPY_POET_PROVIDERS"] = {ZyteApiProvider: 1100}
    settings["DOWNLOAD_HANDLERS"]["http"] = RecordingHandler
    return settings


CUSTOM_HTTP_REQUEST_HEADERS = [
    {
        "name": "Accept",
        "value": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    {"name": "Accept-Language", "value": "en"},
]

CUSTOM_HTTP_REQUEST_HEADERS_2 = [
    {
        "name": "Accept",
        "value": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    {"name": "Accept-Language", "value": "en"},
    {"name": "Accept-Encoding", "value": "gzip, deflate, br"},
]


@ensureDeferred
async def test_provider_any_response_only(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS,
    }
    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == HttpResponse


@ensureDeferred
async def test_provider_any_response_product(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        product: Product

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "product": True,
        "productOptions": {"extractFrom": "httpResponseBody"},
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS,
    }
    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == HttpResponse
    assert type(item["page"].product) == Product


@ensureDeferred
async def test_provider_any_response_product_extract_from_browser_html(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        product: Product

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    product_options = {"extractFrom": "browserHtml"}
    settings = provider_settings(mockserver)
    settings["ZYTE_API_PROVIDER_PARAMS"] = {"productOptions": product_options}
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "product": True,
        "browserHtml": True,
        "productOptions": product_options,
    }

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["page"].product) == Product


@ensureDeferred
async def test_provider_any_response_product_item_extract_from_browser_html(mockserver):
    @attrs.define
    class SomePage(ItemPage[Product]):
        response: AnyResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage, product: Product):
            yield {"page": page, "product": product}

    product_options = {"extractFrom": "browserHtml"}
    settings = provider_settings(mockserver)
    settings["ZYTE_API_PROVIDER_PARAMS"] = {"productOptions": product_options}
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "product": True,
        "browserHtml": True,
        "productOptions": product_options,
    }

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["product"]) == Product


@ensureDeferred
async def test_provider_any_response_product_extract_from_browser_html_2(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        browser_response: BrowserResponse
        product: Product

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    product_options = {"extractFrom": "browserHtml"}
    settings = provider_settings(mockserver)
    settings["ZYTE_API_PROVIDER_PARAMS"] = {"productOptions": product_options}
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "product": True,
        "browserHtml": True,
        "productOptions": product_options,
    }

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["page"].browser_response) == BrowserResponse
    assert type(item["page"].product) == Product

    assert id(item["page"].browser_response) == id(item["page"].response.response)


@ensureDeferred
async def test_provider_any_response_product_extract_from_http_response(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        product: Product

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    product_options = {"extractFrom": "httpResponseBody"}
    settings = provider_settings(mockserver)
    settings["ZYTE_API_PROVIDER_PARAMS"] = {"productOptions": product_options}
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "product": True,
        "httpResponseBody": True,
        "productOptions": product_options,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS,
    }

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == HttpResponse
    assert type(item["page"].product) == Product


# The issue here is that HttpResponseProvider runs earlier than ScrapyZyteAPI.
# HttpResponseProvider doesn't know that it should not run since ScrapyZyteAPI
# could provide HttpResponse in anycase.
@pytest.mark.xfail(reason="Not supported yet", raises=AssertionError, strict=True)
@ensureDeferred
async def test_provider_any_response_product_extract_from_http_response_2(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        http_response: HttpResponse
        product: Product

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    product_options = {"extractFrom": "httpResponseBody"}
    settings = provider_settings(mockserver)
    settings["ZYTE_API_PROVIDER_PARAMS"] = {"productOptions": product_options}
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "product": True,
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "productOptions": product_options,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS,
    }

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == HttpResponse
    assert type(item["page"].product) == Product
    assert type(item["page"].http_response) == HttpResponse


@ensureDeferred
async def test_provider_any_response_browser_html(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        html: BrowserHtml

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {"url": url, "browserHtml": True}

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["page"].html) == BrowserHtml


@ensureDeferred
async def test_provider_any_response_browser_response(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        browser_response: BrowserResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {"url": url, "browserHtml": True}

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["page"].browser_response) == BrowserResponse


@ensureDeferred
async def test_provider_any_response_browser_html_response(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        browser_response: BrowserResponse
        html: BrowserHtml

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {"url": url, "browserHtml": True}

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["page"].browser_response) == BrowserResponse
    assert type(item["page"].html) == BrowserHtml


@ensureDeferred
async def test_provider_any_response_http_response(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        http_response: HttpResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS_2,
    }

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == HttpResponse
    assert type(item["page"].http_response) == HttpResponse


@ensureDeferred
async def test_provider_any_response_browser_http_response(mockserver):
    @attrs.define
    class SomePage(BasePage):
        response: AnyResponse
        browser_response: BrowserResponse
        http_response: HttpResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page: SomePage):
            yield {"page": page}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 2
    assert params[0] == {
        "url": url,
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS_2,
    }
    assert params[1] == {"url": url, "browserHtml": True}

    assert type(item["page"].response) == AnyResponse
    assert type(item["page"].response.response) == BrowserResponse
    assert type(item["page"].browser_response) == BrowserResponse
    assert type(item["page"].http_response) == HttpResponse

    assert id(item["page"].browser_response) == id(item["page"].response.response)


@ensureDeferred
async def test_provider_any_response_http_response_multiple_pages(mockserver):
    @attrs.define
    class FirstPage(BasePage):
        http_response: HttpResponse

    @attrs.define
    class SecondPage(BasePage):
        http_response: HttpResponse
        response: AnyResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page1: FirstPage, page2: SecondPage):
            yield {"page1": page1, "page2": page2}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 1
    assert params[0] == {
        "url": url,
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS_2,
    }
    assert type(item["page1"].http_response) == HttpResponse
    assert type(item["page2"].http_response) == HttpResponse
    assert type(item["page2"].response) == AnyResponse
    assert type(item["page2"].response.response) == HttpResponse


@ensureDeferred
async def test_provider_any_response_http_browser_response_multiple_pages(mockserver):
    @attrs.define
    class FirstPage(BasePage):
        browser_response: BrowserResponse

    @attrs.define
    class SecondPage(BasePage):
        http_response: HttpResponse
        response: AnyResponse

    class ZyteAPISpider(Spider):
        def start_requests(self):
            yield Request(self.url, callback=self.parse_)

        def parse_(self, response: DummyResponse, page1: FirstPage, page2: SecondPage):
            yield {"page1": page1, "page2": page2}

    settings = provider_settings(mockserver)
    item, url, crawler = await crawl_single_item(ZyteAPISpider, HtmlResource, settings)
    params = crawler.engine.downloader.handlers._handlers["http"].params

    assert len(params) == 2
    assert params[0] == {
        "url": url,
        "httpResponseBody": True,
        "httpResponseHeaders": True,
        "customHttpRequestHeaders": CUSTOM_HTTP_REQUEST_HEADERS_2,
    }
    assert params[1] == {"url": url, "browserHtml": True}

    assert type(item["page1"].browser_response) == BrowserResponse
    assert type(item["page2"].http_response) == HttpResponse
    assert type(item["page2"].response) == AnyResponse
    assert type(item["page2"].response.response) == BrowserResponse
