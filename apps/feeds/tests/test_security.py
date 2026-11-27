import socket
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.feeds import login_throttle
from apps.feeds.models import LoginFailure
from apps.feeds.services import http


class PublicAddressTests(SimpleTestCase):
    def test_classification(self):
        for ip, public in [
            ("93.184.215.14", True),
            ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", True),
            ("127.0.0.1", False),
            ("::1", False),
            ("10.1.2.3", False),
            ("172.16.0.1", False),
            ("192.168.1.1", False),
            ("169.254.169.254", False),  # cloud metadata
            ("100.64.0.1", False),  # carrier-grade NAT
            ("0.0.0.0", False),
            ("fe80::1%eth0", False),
            ("fd00::1", False),
            ("::ffff:127.0.0.1", False),
            ("::ffff:93.184.215.14", True),
            ("not an address", False),
        ]:
            with self.subTest(ip):
                self.assertIs(http.is_public_address(ip), public)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        type(self).requests.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2:9/elsewhere")
            self.end_headers()
            return
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class PrivateAddressGuardTests(SimpleTestCase):
    """Real requests to a server on 127.0.0.1, which the guard must refuse."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def setUp(self):
        _Handler.requests = []

    def url(self, path="/", host="127.0.0.1"):
        return f"http://{host}:{self.port}{path}"

    def test_loopback_refused_without_a_request(self):
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host), self.assertRaisesMessage(http.FetchError, "not a public internet address"):
                http.get(self.url(host=host))
        self.assertEqual(_Handler.requests, [])

    def test_redirect_to_a_private_address_refused(self):
        # Pretend 127.0.0.1 is public so the first hop goes through.
        with mock.patch.object(http, "is_public_address", lambda ip: ip == "127.0.0.1"):
            with self.assertRaisesMessage(http.FetchError, "127.0.0.2 is not a public internet address"):
                http.get(self.url("/redirect"))
        self.assertEqual(_Handler.requests, ["/redirect"])

    def test_dns_answer_that_changes_is_caught_after_connecting(self):
        # The first lookup (the up-front check) says public; the one the connection
        # uses says 127.0.0.1, as a DNS rebinding attack would.
        real = socket.getaddrinfo
        lookups = []

        def getaddrinfo(host, port, *args, **kwargs):
            if host == "rebind.test":
                lookups.append(host)
                if len(lookups) == 1:
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))]
                host = "127.0.0.1"
            return real(host, port, *args, **kwargs)

        with mock.patch("socket.getaddrinfo", side_effect=getaddrinfo):
            with self.assertRaisesMessage(http.FetchError, "rebind.test is at 127.0.0.1"):
                http.get(self.url(host="rebind.test"))
        self.assertEqual(len(lookups), 2)
        self.assertEqual(_Handler.requests, [])

    @override_settings(READER_ALLOW_PRIVATE_ADDRESSES=True)
    def test_setting_allows_private_addresses(self):
        result = http.get(self.url())
        self.assertEqual((result.status, result.content), (200, b"hello"))


# A fast hasher: the real one takes a second or two per attempt, and these tests make dozens.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LoginThrottleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("me", password="pw-for-tests-only")
        self.login_url = reverse("login")

    def attempt(self, password="wrong", url=None, **extra):
        return self.client.post(url or self.login_url, {"username": "me", "password": password}, **extra)

    def fail(self, times, **extra):
        for _ in range(times):
            self.assertContains(self.attempt(**extra), "That username and password didn")

    def test_too_many_failures_refuse_even_the_right_password(self):
        self.fail(login_throttle.MAX_FAILURES)
        response = self.attempt("pw-for-tests-only")
        self.assertContains(response, "Too many failed sign-ins from your address. Try again in 15 minutes.")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(LoginFailure.objects.count(), login_throttle.MAX_FAILURES)  # refusals aren't counted

    def test_other_addresses_unaffected(self):
        self.fail(login_throttle.MAX_FAILURES, HTTP_X_REAL_IP="198.51.100.7")
        response = self.attempt("pw-for-tests-only", HTTP_X_REAL_IP="198.51.100.8")
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_real_ip_header_only_trusted_from_the_proxy(self):
        self.attempt(REMOTE_ADDR="203.0.113.9", HTTP_X_REAL_IP="198.51.100.1")
        self.attempt(HTTP_X_REAL_IP="198.51.100.2")  # from 127.0.0.1, as nginx sends it
        self.attempt(HTTP_X_REAL_IP="nonsense")
        self.assertEqual(
            sorted(LoginFailure.objects.values_list("ip", flat=True)), ["127.0.0.1", "198.51.100.2", "203.0.113.9"]
        )

    def test_success_clears_failures_but_a_blank_password_does_not(self):
        self.fail(3)
        self.client.post(self.login_url, {"username": "me", "password": ""})
        self.assertEqual(LoginFailure.objects.count(), 3)
        self.attempt("pw-for-tests-only")
        self.assertEqual(LoginFailure.objects.count(), 0)

    def test_old_failures_expire(self):
        self.fail(login_throttle.MAX_FAILURES)
        LoginFailure.objects.update(at=timezone.now() - login_throttle.WINDOW - timedelta(seconds=1))
        self.assertRedirects(self.attempt("pw-for-tests-only"), "/", fetch_redirect_response=False)

    def test_wait_counts_from_the_oldest_failure(self):
        self.fail(login_throttle.MAX_FAILURES)
        LoginFailure.objects.update(at=timezone.now() - timedelta(minutes=14, seconds=30))
        self.assertContains(self.attempt(), "Try again in 1 minute.")

    def test_admin_login_is_throttled_too(self):
        admin_login = reverse("admin:login")
        self.fail(login_throttle.MAX_FAILURES)
        self.assertContains(self.attempt(url=admin_login), "Too many failed sign-ins from your address.")
