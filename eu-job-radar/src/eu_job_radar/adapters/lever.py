"""Lever Postings API: GET /v0/postings/{token}?mode=json.

Public and keyless for published postings. Accounts hosted in Lever's EU
region use api.eu.lever.co (set `region = "eu"` on the board). Each posting
gives id, text (the title), hostedUrl, categories (location, allLocations,
team, department, commitment), workplaceType and createdAt in milliseconds.
Without `limit` the API returns every published posting.
"""

from . import (
    Adapter,
    Board,
    BoardResult,
    clean_text,
    iso_from_millis,
    posting,
    quarantine,
    token_path,
    valid_id,
)


class LeverAdapter(Adapter):
    ats = "lever"
    label = "Lever"
    hosts = ["api.lever.co", "api.eu.lever.co"]

    def host(self, board: Board) -> str:
        return "api.eu.lever.co" if board.region == "eu" else "api.lever.co"

    def url(self, board: Board) -> str:
        return f"https://{self.host(board)}/v0/postings/{token_path(board.token)}?mode=json"

    def parse(self, data, board: Board) -> BoardResult:
        if isinstance(data, dict) and data.get("ok") is False:
            raise ValueError(str(data.get("error") or "API returned ok=false"))
        if not isinstance(data, list):
            raise ValueError("expected a list of postings")
        result = BoardResult("complete")
        for item in data:
            if not isinstance(item, dict):
                quarantine(result, "item is not an object", {})
                continue
            if not valid_id(item.get("id")) or not clean_text(item.get("text")):
                quarantine(result, "missing id or title", item)
                continue
            categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
            names = [categories.get("location")]
            names += [n for n in categories.get("allLocations") or [] if isinstance(n, str)]
            workplace = (item.get("workplaceType") or "").lower()
            result.postings.append(
                posting(
                    record_id=item["id"],
                    title=item["text"],
                    url=item.get("hostedUrl"),
                    locations=names,
                    remote=True if workplace == "remote" else None,
                    department=categories.get("department") or categories.get("team"),
                    employment_type=categories.get("commitment"),
                    published_at=iso_from_millis(item.get("createdAt")),
                )
            )
        return result
