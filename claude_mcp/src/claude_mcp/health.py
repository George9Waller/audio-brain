"""INFRA.md's I9: how the server is doing, in one tool. The last week's nightly runs as a table (one row a night, a
column a job, and what failed them), then the warnings, how old the models are, the enrichment backlog, the sizes
and the workers."""

from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.types import CallToolResult

from claude_mcp.reasoning import READ, number, result, table


def when(value, zone):
    return datetime.fromisoformat(value).astimezone(zone)


def duration(seconds):
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 90 else f"{minutes // 60}h{minutes % 60:02}m"


def cell(run, zone):
    """'ok 48 min', 'failed 03:09', 'running since 03:00'; a retry says so."""
    start = when(run["started_at"], zone).strftime("%H:%M")
    if run["state"] == "running":
        text = f"running since {start}"
    elif run["state"] == "failed":
        text = f"failed {start}"
    else:
        text = f"ok {duration(run['seconds'])}" if run["seconds"] is not None else run["state"]
    return f"retry {text}" if run["retry"] else text


def megabytes(mb):
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"


def summary(data):
    zone = ZoneInfo(data["time_zone"])
    jobs = list(data["nights"][0]["runs"]) if data["nights"] else []
    rows = []
    for night in data["nights"]:
        runs = night["runs"]
        causes = [f"{job}: {run['cause']}" for job in jobs for run in runs[job] if run["cause"]]
        day = datetime.fromisoformat(night["night"])
        rows.append([f"{day:%a} {day.day} {day:%b}"] + [", ".join(cell(r, zone) for r in runs[job]) or "–" for job in jobs]
                    + ["; ".join(causes)])
    parts = []
    if data["warnings"]:
        parts.append("**Warnings**\n" + "\n".join(f"- {w['text']}" for w in data["warnings"]))
    else:
        age = f"; models rebuilt {data['models_age_hours']:.0f} h ago" if data["models_age_hours"] is not None else ""
        parts.append(f"No warnings: every scheduled job's latest run succeeded and every worker is running{age}.")
    parts.append(table(["Night", *jobs, "Cause"], rows))

    latest = next((n for n in data["nights"] if any(n["runs"].values())), None)
    if latest:
        peaks = [(job, max(measured, key=lambda r: r["peak_mb"])) for job, runs in latest["runs"].items()
                 if (measured := [r for r in runs if r["peak_mb"]])]
        peaks = sorted(peaks, key=lambda p: -p[1]["peak_mb"])
        if peaks:
            parts.append("Peak memory on the latest night: " + ", ".join(f"{job} {megabytes(run['peak_mb'])} ({run['peak_step']})" for job, run in peaks))
    if data["models_rebuilt_at"]:
        rebuilt = when(data["models_rebuilt_at"], zone)
        parts.append(f"Models last rebuilt {rebuilt:%a} {rebuilt.day} {rebuilt:%b} at {rebuilt:%H:%M} ({data['models_age_hours']:.0f} h ago).")
    backlog = {stage: count for stage, count in data["backlog"].items() if count}
    parts.append("Enrichment backlog: " + (", ".join(f"{stage} {number(count)}" for stage, count in backlog.items()) or "none") + ".")
    c = data["catalogue"]
    parts.append(f"Catalogue: {number(c['tracks'])} tracks, {number(c['analysed'])} analysed, highest ID {number(c['highest_id'])}.")
    if data["databases"]:
        parts.append("Databases: " + ", ".join(f"{d['name']} {megabytes(d['mb'])}" for d in data["databases"]) + ".")
    lanes = []
    for w in data["workers"]:
        if not w["alive"]:
            lanes.append(f"{w['lane']} not running")
        elif w["started_at"]:
            started = when(w["started_at"], zone)
            lanes.append(f"{w['lane']} up since {started:%a} {started.day} {started:%b} {started:%H:%M}")
    deaths = data["deaths"]
    parts.append("Workers: " + "; ".join(lanes) + f". Runs in the last week lost to a worker stopping: {deaths['worker_died']}; "
                 f"killed for memory: {deaths['killed_for_memory']}.")
    return "\n\n".join(parts)


def register(mcp, api):
    @mcp.tool(title="Server health", annotations=READ,
              description="How the audio-brain server is doing: the last week's nightly runs (enrichment, models, mixes, "
                          "candidates) and what failed them, warnings (models out of date, failed jobs, workers down), the "
                          "enrichment backlog, the catalogue's and the databases' sizes, peak memory, and the workers.")
    async def server_health() -> CallToolResult:
        data = await api.get("/health/detail")
        return result(summary(data), data)
