import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import urljoin


class MockServer:
    def __enter__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _RequestHandler)
        self.address, self.port = self.httpd.server_address
        self.thread = Thread(target=self.httpd.serve_forever)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.httpd.shutdown()
        self.thread.join()

    def urljoin(self, url: str) -> str:
        return urljoin("http://{}:{}".format(self.address, self.port), url)


class _RequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # NOQA
        # content_length = int(self.headers['Content-Length'])  # <--- Gets the size of data
        # post_data = self.rfile.read(content_length)  # <--- Gets the data itself
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"browserHtml": "<html></html>"}).encode("utf-8"))
