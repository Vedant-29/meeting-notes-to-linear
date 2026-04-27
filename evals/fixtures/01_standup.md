# Standup — Tuesday

me: yesterday finally got the auth refactor merged, sat in review for like 9 days lol. today gonna look at why the staging deploy is taking 14 min when prod is 4. probably the new image build but who knows. blocked-ish on design for the new settings page, alex said EOW

priya: shipped the rate limit thing last night. seeing weird 429s on /api/notes from the mobile app though, might be too aggressive, need to check the per-route config. also reminder we are doing the postgres 16 upgrade friday morning, will send the maintenance window email today

dan: working on the import-from-notion flow, probably 2 more days. the markdown parser is choking on their callout blocks. also btw the analytics dashboard chart is showing wrong numbers since last deploy, p2 not p1 but should look at it this week
