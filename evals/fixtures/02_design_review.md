# Design review — onboarding redesign v2

attendees: priya (PM), alex (design), me, dan

alex walked through the new flow. 4 screens instead of 7. big improvement. some notes from discussion:

- the email step still has both "magic link" and "password" options on the same screen, alex wants to A/B but eng pushback that we'd need to maintain both indefinitely. priya: let's just commit to magic link only for new signups, existing users keep password until next reset. ACTION: update spec doc, update auth backend to support password-less new accounts
- the sample data step is great visually but we have no actual sample data fixtures in staging or prod. dan said he can write a seed script but it'd take a couple days. we agreed to ship without sample data first and add it in a follow-up
- the welcome video — alex pushing for one, nobody on the team can record it. priya will ask marketing
- mobile flow: the second screen completely breaks on iOS small (iPhone SE). like literally the button is offscreen. needs fixing before we can ship anywhere
- analytics: we need to instrument every step transition with posthog events so we can measure drop-off. currently no instrumentation in onboarding at all
- accessibility: alex hasn't done a a11y pass yet. agreed to bring in the contractor we used last quarter

decision: target ship date is 2 weeks out, may 11. priya owns the spec update, alex owns design polish + a11y, dan owns sample data follow-up, me on auth changes + posthog wiring + mobile fix

post-meeting: realized we never decided what happens if someone abandons mid-flow. do we email them? save state? throw it all away? need to figure out before launch
