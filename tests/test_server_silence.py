"""Tests for the ConnectionResetError silencing layers.

Two complementary mechanisms are in place:
  1. A loop-level exception handler (defence in depth)
  2. A process-wide logging filter that inspects record.exc_info

Both are tested here — they must catch the same noise but via different paths.
"""
import asyncio
import logging
import sys
import unittest

from src.api.server import (
    _silence_asyncio_connection_reset,
    _install_global_connection_reset_filter,
)


class SilenceConnectionResetTests(unittest.TestCase):
    def _make_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.new_event_loop()
        _silence_asyncio_connection_reset(loop)
        return loop

    def test_swallows_connection_reset_error(self) -> None:
        loop = self._make_loop()
        calls: list[dict] = []
        # Stub the default exception handler to detect any leak
        orig_default = loop.default_exception_handler
        loop.default_exception_handler = lambda ctx: calls.append(ctx)  # type: ignore[assignment]
        try:
            loop.call_exception_handler({
                "message": "Exception in callback",
                "exception": ConnectionResetError(10054, "Connection reset"),
            })
            loop.call_exception_handler({
                "message": "Exception in callback",
                "exception": ConnectionAbortedError(10053, "Connection aborted"),
            })
            # Both should be silently swallowed
            self.assertEqual(calls, [])
        finally:
            loop.default_exception_handler = orig_default  # type: ignore[assignment]
            loop.close()

    def test_other_exceptions_pass_through_to_default(self) -> None:
        loop = self._make_loop()
        seen: list[dict] = []
        loop.default_exception_handler = lambda ctx: seen.append(ctx)  # type: ignore[assignment]
        try:
            loop.call_exception_handler({
                "message": "Real bug",
                "exception": ValueError("something else"),
            })
            self.assertEqual(len(seen), 1)
            self.assertIsInstance(seen[0]["exception"], ValueError)
        finally:
            loop.close()

    def test_winerror_10054_message_also_silenced_without_exception(self) -> None:
        # Some asyncio paths report the error via the `message` field only
        loop = self._make_loop()
        seen: list[dict] = []
        loop.default_exception_handler = lambda ctx: seen.append(ctx)  # type: ignore[assignment]
        try:
            loop.call_exception_handler({
                "message": "socket.shutdown failed: [WinError 10054] ...",
            })
            self.assertEqual(seen, [])
        finally:
            loop.close()


class GlobalLoggingFilterTests(unittest.TestCase):
    """Tests the logging filter — the primary defence since asyncio's
    default_exception_handler emits via logger.error(msg, exc_info=...)."""

    def setUp(self) -> None:
        # Ensure filters are installed (idempotent since they're attached at import)
        _install_global_connection_reset_filter()
        self.logger = logging.getLogger("asyncio")
        self.captured: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda rec: self.captured.append(rec)  # type: ignore[method-assign]
        self.logger.addHandler(self._handler)
        # Set level so records aren't dropped before reaching the filter chain
        self._orig_level = self.logger.level
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self) -> None:
        self.logger.removeHandler(self._handler)
        self.logger.setLevel(self._orig_level)

    def _make_exc_info(self, exc: BaseException) -> tuple:
        try:
            raise exc
        except BaseException:
            return sys.exc_info()

    def test_filter_drops_connection_reset_with_exc_info(self) -> None:
        self.logger.error("Exception in callback ...",
                          exc_info=self._make_exc_info(ConnectionResetError(10054, "reset")))
        self.assertEqual(self.captured, [],
                         "ConnectionResetError record should have been filtered out")

    def test_filter_drops_connection_aborted_with_exc_info(self) -> None:
        self.logger.error("Exception in callback ...",
                          exc_info=self._make_exc_info(ConnectionAbortedError(10053, "abort")))
        self.assertEqual(self.captured, [])

    def test_filter_preserves_unrelated_errors(self) -> None:
        self.logger.error("Real bug",
                          exc_info=self._make_exc_info(ValueError("real problem")))
        self.assertEqual(len(self.captured), 1)
        self.assertIsInstance(self.captured[0].exc_info[1], ValueError)

    def test_filter_drops_winerror_message_without_exc_info(self) -> None:
        # When the noise comes through as a plain message (no exc_info)
        self.logger.error("socket shutdown failed: [WinError 10054] cancellation")
        self.assertEqual(self.captured, [])


if __name__ == "__main__":
    unittest.main()
