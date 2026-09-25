"""Ashby public job posting API: GET /posting-api/job-board/{token}.

Public and keyless. Each job gives id, title, location, secondaryLocations,
department, team, isRemote, employmentType, publishedAt, jobUrl and isListed.
Unlisted jobs (isListed false) are skipped: the company has not published them.
"""

from . import Adapter, Board, BoardResult, iso_text, posting, quarantine, token_path, valid_id


class AshbyAdapter(Adapter):
    ats = "ashby"
    label = "Ashby"
    hosts = ["api.ashbyhq.com"]

    def url(self, board: Board) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{token_path(board.token)}"

    def parse(self, data, board: Board) -> BoardResult:
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError("expected an object with a 'jobs' list")
        result = BoardResult("complete")
        for item in data["jobs"]:
            if not isinstance(item, dict):
                quarantine(result, "item is not an object", {})
                continue
            if item.get("isListed") is False:
                continue
            if not valid_id(item.get("id")) or not item.get("title"):
                quarantine(result, "missing id or title", item)
                continue
            names = [item.get("location")]
            for extra in item.get("secondaryLocations") or []:
                if isinstance(extra, dict):
                    names.append(extra.get("location"))
            address = (
                ((item.get("address") or {}).get("postalAddress") or {})
                if isinstance(item.get("address"), dict)
                else {}
            )
            if isinstance(address, dict) and address.get("addressCountry"):
                names.append(address.get("addressCountry"))
            result.postings.append(
                posting(
                    record_id=item["id"],
                    title=item["title"],
                    url=item.get("jobUrl"),
                    locations=names,
                    remote=True if item.get("isRemote") is True else None,
                    department=item.get("department") or item.get("team"),
                    employment_type=item.get("employmentType"),
                    published_at=iso_text(item.get("publishedAt")),
                )
            )
        return result
