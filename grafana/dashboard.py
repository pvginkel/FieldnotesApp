"""Writes `fieldnotes.json`, the Grafana dashboard over the API's `/metrics` (docs/rest-api.md,
"Metrics"), and with `--upload` puts it into the Grafana at `$GRAFANA_URL` with `$GRAFANA_TOKEN`.

`uv run grafana/dashboard.py [--upload]` from the repo root.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

DATASOURCE = {"type": "prometheus", "uid": "ce0kvu6exy9z4c"}
OUT = Path(__file__).with_name("fieldnotes.json")

API_ROUTES = 'route!~"/metrics|/healthz|/readyz|unmatched"'

_ids = iter(range(1, 1000))


def target(expr, legend="", **extra):
    return {"datasource": DATASOURCE, "expr": expr, "legendFormat": legend, "refId": "A", **extra}


def panel(kind, title, x, y, w, h, targets, description="", **extra):
    return {
        "id": next(_ids),
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DATASOURCE,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{**t, "refId": chr(65 + n)} for n, t in enumerate(targets)],
        **extra,
    }


def stat(title, x, y, expr, description, unit="short", decimals=0):
    return panel(
        "stat",
        title,
        x,
        y,
        4,
        4,
        [target(expr, instant=True)],
        description,
        fieldConfig={"defaults": {"unit": unit, "decimals": decimals}, "overrides": []},
        options={"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "none"},
    )


def bars(title, x, y, w, expr, legend, description, interval="1h"):
    """Counts per `interval`, stacked."""
    return panel(
        "timeseries",
        title,
        x,
        y,
        w,
        8,
        [target(expr, legend)],
        description,
        interval=interval,
        fieldConfig={
            "defaults": {
                "custom": {
                    "drawStyle": "bars",
                    "fillOpacity": 80,
                    "lineWidth": 0,
                    "stacking": {"mode": "normal"},
                },
                "decimals": 0,
            },
            "overrides": [],
        },
        options={"legend": {"displayMode": "list", "placement": "bottom"}},
    )


def lines(title, x, y, w, expr, legend, description, unit="short", stacked=False):
    custom = {
        "fillOpacity": 30 if stacked else 0,
        "stacking": {"mode": "normal" if stacked else "none"},
    }
    return panel(
        "timeseries",
        title,
        x,
        y,
        w,
        8,
        [target(expr, legend)],
        description,
        fieldConfig={"defaults": {"unit": unit, "custom": custom}, "overrides": []},
        options={"legend": {"displayMode": "list", "placement": "bottom"}},
    )


def ranking(title, x, y, w, expr, legend, description, heatmap=False):
    return panel(
        "bargauge",
        title,
        x,
        y,
        w,
        8,
        [target(expr, legend, instant=True, format="heatmap" if heatmap else "time_series")],
        description,
        fieldConfig={"defaults": {"decimals": 0, "min": 0}, "overrides": []},
        options={
            "orientation": "horizontal",
            "displayMode": "basic",
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "showUnfilled": True,
        },
    )


def row(title, y):
    return {
        "id": next(_ids),
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def inc(metric, selector="", by="", window="$__range"):
    grouping = f" by ({by})" if by else ""
    return f"sum{grouping} (increase({metric}{{{selector}}}[{window}]))"


def dashboard():
    posts = "fieldnotes_posts_total"
    follow_ups = "fieldnotes_match_follow_ups_total"
    requests = "fieldnotes_http_request_duration_seconds"
    panels = [
        row("Does the answer land", 0),
        stat("Posts", 0, 1, inc(posts), "Posts in the range, created, matched and forced alike."),
        stat(
            "Answered from the store",
            4,
            1,
            inc(posts, 'outcome="matched"') + " / " + inc(posts),
            "The share of posts that were answered with candidates and created nothing.",
            unit="percentunit",
        ),
        stat(
            "Answer landed",
            8,
            1,
            inc(follow_ups, 'result="reacted"') + " / " + inc(follow_ups),
            "Of the matched posts that were settled, the share whose reporter reacted to a "
            "candidate it was offered. The rest reacted elsewhere, forced, reposted or left it.",
            unit="percentunit",
        ),
        stat("Reactions", 12, 1, inc("fieldnotes_reactions_total"), "Reactions in the range."),
        stat(
            "Open observations",
            16,
            1,
            'sum(fieldnotes_observations{status="open"})',
            "Open observations in the store now: posted, not yet proposed or raised.",
        ),
        stat("In the store", 20, 1, "sum(fieldnotes_observations)", "Every observation now."),
        bars(
            "Posts by outcome",
            0,
            5,
            12,
            inc(posts, by="outcome", window="$__interval"),
            "{{outcome}}",
            "created: nothing matched. matched: candidates came back and nothing was created. "
            "forced: the reporter posted with force.",
        ),
        bars(
            "After a matched post",
            12,
            5,
            12,
            inc(follow_ups, by="result", window="$__interval"),
            "{{result}}",
            "What the same reporter (repo and session) did next, within 30 minutes. reacted: to "
            "a candidate it was offered. reacted_other: to another observation. forced, reposted, "
            "or abandoned: nothing in the window.",
        ),
        ranking(
            "Best candidate's score",
            0,
            13,
            12,
            "sum by (le) (increase(fieldnotes_post_top_score_bucket[$__range]))",
            "{{le}}",
            "Matched posts by their best candidate's score, per bucket, over the range: where "
            "the matches sit against the thresholds.",
            heatmap=True,
        ),
        ranking(
            "Candidates by match class",
            12,
            13,
            12,
            inc("fieldnotes_post_candidates_total", by="match_class"),
            "{{match_class}}",
            "Candidates returned to matched posts over the range.",
        ),
        row("The store", 21),
        lines(
            "Observations by status",
            0,
            22,
            12,
            "sum by (status) (fieldnotes_observations)",
            "{{status}}",
            "The store's observations, from the index.",
            stacked=True,
        ),
        lines(
            "Observations by category",
            12,
            22,
            12,
            "sum by (category) (fieldnotes_observations)",
            "{{category}}",
            "The store's observations, from the index.",
            stacked=True,
        ),
        ranking(
            "Posts in the store by repo",
            0,
            30,
            8,
            'sort_desc(sum by (repo) (fieldnotes_store_reactions{emoji="📝"}))',
            "{{repo}}",
            "The observations each repo posted that are still in the store. Retired ones leave it.",
        ),
        ranking(
            "Reactions in the store by repo",
            8,
            30,
            8,
            'sort_desc(sum by (repo) (fieldnotes_store_reactions{emoji!="📝"}))',
            "{{repo}}",
            "Reactions each repo left on observations still in the store, posts left out.",
        ),
        ranking(
            "Reactions in the store by emoji",
            16,
            30,
            8,
            'sort_desc(sum by (emoji) (fieldnotes_store_reactions{emoji!="📝"}))',
            "{{emoji}}",
            "Reactions on observations still in the store, posts left out.",
        ),
        row("The service", 38),
        lines(
            "Requests",
            0,
            39,
            8,
            f"sum by (route) (rate({requests}_count{{{API_ROUTES}}}[$__rate_interval]))",
            "{{route}}",
            "Requests per second by route; health checks and scrapes left out.",
            unit="reqps",
        ),
        lines(
            "Latency p95",
            8,
            39,
            8,
            "histogram_quantile(0.95, sum by (le, route) "
            f"(rate({requests}_bucket{{{API_ROUTES}}}[$__rate_interval])))",
            "{{route}}",
            "The 95th percentile by route. A post embeds its text on the models pod first.",
            unit="s",
        ),
        bars(
            "Errors",
            16,
            39,
            8,
            inc(f"{requests}_count", 'status=~"[45].."', by="route, status", window="$__interval"),
            "{{status}} {{route}}",
            "4xx and 5xx answers by route and status. A 401 on a webhook is a refused delivery.",
        ),
        bars(
            "Webhook deliveries",
            0,
            47,
            8,
            inc("fieldnotes_webhook_deliveries_total", by="source, action", window="$__interval"),
            "{{source}} {{action}}",
            "Verified deliveries: queued work, or ignored as none of ours.",
        ),
        bars(
            "Board syncs",
            8,
            47,
            8,
            inc("fieldnotes_board_syncs_total", by="result", window="$__interval"),
            "{{result}}",
            "Syncs of an observation's card: changed, unchanged, or failed when queued.",
        ),
        bars(
            "Reads by id",
            16,
            47,
            8,
            inc("fieldnotes_gets_total", by="client", window="$__interval"),
            "{{client}}",
            "Observations read in full, by client: mcp is an agent's get, skills the reconciler.",
        ),
    ]
    return {
        "uid": "fieldnotes",
        "title": "Fieldnotes",
        "description": "Whether the post-time answer lands, the store, and the API's health.",
        "tags": ["fieldnotes"],
        "timezone": "browser",
        "refresh": "5m",
        "time": {"from": "now-7d", "to": "now"},
        "schemaVersion": 39,
        "panels": panels,
    }


def main():
    board = dashboard()
    OUT.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n")
    if "--upload" in sys.argv:
        body = json.dumps({"dashboard": board, "overwrite": True}).encode()
        request = urllib.request.Request(
            os.environ["GRAFANA_URL"].rstrip("/") + "/api/dashboards/db",
            data=body,
            headers={
                "Authorization": f"Bearer {os.environ['GRAFANA_TOKEN']}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request) as response:
            print(json.load(response))


if __name__ == "__main__":
    main()
