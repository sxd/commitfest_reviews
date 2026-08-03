# CommitFest review publication

This repository publishes the canonical, final PostgreSQL CommitFest reports
created by Amauta's review harness.  It deliberately excludes specialist
handoffs, thread ledgers, and failed execution artifacts.

`sync-commitfest-reviews.py` reads the local `postgresql-master` Amauta source,
accepting only a canonical `commitfest-<patch>-review` report with
`schema: review-report-v2`. It fetches each report's browser-ready HTML from
Amauta's local safe renderer, rather than publishing escaped Markdown. It then
commits changes to `docs/` and pushes `main`; the GitHub Pages workflow publishes
the result at <https://code.emacs.cl/commitfest_reviews/>.

## Schedule

`amauta-commitfest-review.timer` is managed with the review runner on
`commitfest-amauta`. It starts one eligible CommitFest review every two hours,
with up to ten minutes of randomized delay. An active review is never run a
second time concurrently.

This repository's separate publication timer exports completed final reports
every six hours:

```sh
cd /home/ubuntu/commitfest_reviews
./install-systemd-user.sh
systemctl --user list-timers commitfest-reviews-sync.timer
```

Run a safe render-only preview with:

```sh
./sync-commitfest-reviews.py --no-push
```
