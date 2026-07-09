import httpx
import time

from app.config import get_settings


class ThreadsPostingError(RuntimeError):
    pass


def _publish_text(content: str, reply_to_id: str | None = None, retries: int = 3, account: dict | None = None) -> dict:
    settings = get_settings()

    base_url = settings.threads_api_base_url.rstrip("/")
    user_id = str((account or {}).get("user_id") or settings.threads_user_id or "").strip()
    token = str((account or {}).get("access_token") or settings.threads_access_token or "").strip()

    if not token or not user_id:
        raise ThreadsPostingError("Threads access_token and user_id are required to post to Threads.")

    with httpx.Client(timeout=30) as client:
        for attempt in range(1, retries + 1):
            container_data = {
                "media_type": "TEXT",
                "text": content,
                "access_token": token,
            }
            if reply_to_id:
                container_data["reply_to_id"] = reply_to_id

            try:
                container_response = client.post(
                    f"{base_url}/{user_id}/threads",
                    data=container_data,
                )
                container_response.raise_for_status()
                creation_id = container_response.json().get("id")

                if not creation_id:
                    raise ThreadsPostingError("Threads API did not return a creation container id.")

                time.sleep(1)
                publish_response = client.post(
                    f"{base_url}/{user_id}/threads_publish",
                    data={
                        "creation_id": creation_id,
                        "access_token": token,
                    },
                )
                publish_response.raise_for_status()
                return publish_response.json()
            except httpx.HTTPStatusError as exc:
                is_last = attempt >= retries
                response_text = exc.response.text
                is_retryable = exc.response.status_code >= 500 or '"code":24' in response_text or "resource does not exist" in response_text.lower()
                if is_last or not is_retryable:
                    raise ThreadsPostingError(f"Threads API error: {response_text}") from exc
                time.sleep(2 * attempt)
            except httpx.HTTPError as exc:
                if attempt >= retries:
                    raise ThreadsPostingError(f"Cannot call Threads API: {exc}") from exc
                time.sleep(2 * attempt)

    raise ThreadsPostingError("Threads API error: exhausted retries.")


def create_post(content: str, account: dict | None = None) -> dict:
    return _publish_text(content, account=account)


def create_reply(parent_post_id: str, content: str, account: dict | None = None) -> dict:
    return _publish_text(content, reply_to_id=parent_post_id, account=account)
