"""Error tracking (optional): set SENTRY_DSN and errors from the web app, the API and background jobs are reported.
No personal data is sent (send_default_pii=False; request bodies, cookies and user names are not attached)."""
import logging
import os

log = logging.getLogger("stockflow")
_DONE = False


def init(service):
    global _DONE
    if _DONE:
        return False
    _DONE = True
    logging.basicConfig(level=os.environ.get("STOCKFLOW_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=dsn, send_default_pii=False, traces_sample_rate=float(os.environ.get("SENTRY_TRACES", "0")),
                        environment=os.environ.get("STOCKFLOW_ENV", "production"), release=os.environ.get("STOCKFLOW_RELEASE"),
                        server_name=service, max_request_body_size="never")
        log.info("error tracking on (%s)", service)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("error tracking not started: %s", e)
        return False
