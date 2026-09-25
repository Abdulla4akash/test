"""Greenhouse Job Board API: GET /v1/boards/{token}/jobs.

Public and keyless for published jobs. The list gives each job's id, title,
absolute_url, location.name and updated_at; `meta.total` (when present)
states how many jobs the board holds, so a short list is reported as partial.
Job descriptions are not requested (`content` is left off).
"""

from . import Adapter, Board, BoardResult, iso_text, posting, quarantine, token_path, valid_id


class GreenhouseAdapter(Adapter):
    ats = "greenhouse"
    label = "Greenhouse"
    hosts = ["boards-api.greenhouse.io"]

    def url(self, board: Board) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{token_path(board.token)}/jobs"

    def parse(self, data, board: Board) -> BoardResult:
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError("expected an object with a 'jobs' list")
        result = BoardResult("complete")
        for item in data["jobs"]:
            if not isinstance(item, dict):
                quarantine(result, "item is not an object", {})
                continue
            if not valid_id(item.get("id")) or not item.get("title"):
                quarantine(result, "missing id or title", item)
                continue
            location = item.get("location") or {}
            names = [location.get("name")] if isinstance(location, dict) else []
            for office in item.get("offices") or []:
                if isinstance(office, dict):
                    names.append(office.get("location") or office.get("name"))
            result.postings.append(
                posting(
                    record_id=item["id"],
                    title=item["title"],
                    url=item.get("absolute_url"),
                    locations=names,
                    published_at=iso_text(item.get("first_published")),
                    source_updated_at=iso_text(item.get("updated_at")),
                )
            )
        total = (
            (data.get("meta") or {}).get("total") if isinstance(data.get("meta"), dict) else None
        )
        if isinstance(total, int) and total > len(data["jobs"]):
            result.outcome = "partial"
            result.detail = f"board reports {total} jobs but returned {len(data['jobs'])}"
        return result
