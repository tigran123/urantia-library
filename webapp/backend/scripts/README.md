# `scripts/`

Long-running maintenance jobs that ship with the backend and are driven by
systemd timers checked into `webapp/`. Each is a single-purpose shell script,
deliberately separate from `main.py` so the retention policy and the GeoIP
refresh schedule live as auditable artefacts on disk rather than as
in-process decisions.

| Script | Timer | Purpose |
|---|---|---|
| `nightly_backup.sh` | `urantia-library-backup.timer` (03:00 UTC daily) | Online `.backup` snapshot of `lib.db`, 14-day rotation. |
| `prune_usage_events.sh` | `urantia-library-prune.timer` (04:00 UTC daily) | Delete `usage_events` rows older than 24 months. |
| `update_geoip.sh` | `urantia-library-geoip.timer` (05:00 UTC monthly, 1st) | Re-download MaxMind GeoLite2-City.mmdb. |

## Usage analytics — retention rationale

`usage_events` records personal data under GDPR (IP address; see CJEU
*Breyer*, C-582/14). Retention is **24 months**, then pruned. This is
defensible because:

- The processing has a clear, documented purpose (operating, securing, and
  improving the library) and a lawful basis (legitimate interest,
  Art. 6(1)(f) — disclosed in the Privacy Policy §4).
- The processing is **not high-risk**:
  - No special-category data (Art. 9): we never collect race, health,
    religion, political opinions, biometrics, etc.
  - No automated decision-making (Art. 22): nothing in the system decides
    anything about a user from these rows. They are admin-curiosity
    aggregates and abuse forensics only.
  - Limited user base (registration-gated, admin-approved). Most rows
    belong to known users with accounts; guest rows are bounded by the
    public clearance-0 surface, which is small.
  - Fully self-hosted, no third-party transfer (GeoIP runs offline against
    a local MaxMind database).
- Subject rights are operationalised. Logged-in users can view, export
  (Art. 20 portability), and delete (Art. 17 erasure) their own rows
  through `#/account/activity`. Guests' rows are pruned at 24 months by
  this script regardless of any request, and ad-hoc requests are honoured
  via the contact-admin path documented in the Privacy Policy.

A formal Data Protection Impact Assessment (Art. 35) is not required at this
risk level; this README serves as the internal record of the analysis.

If you change the retention window, also change Privacy Policy §6 — they
must always agree. Code without policy is a leak; policy without code is a
lie.
