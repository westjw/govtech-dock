# The new-openings alert: what it is for

A statement of purpose, written before redesign so the design has something
to be right about. Describes `scripts/alert.py`, opened as a GitHub issue by
`.github/workflows/refresh.yml` after each daily refresh.

## What it is

**A work order for one person, generated once a day, listing the companies
whose front door opened since yesterday.**

It is not a newsletter, a digest, or a summary of the board. The board is on
the web and can be browsed at any time. This exists because a door that opened
today is worth acting on today, and nothing else tells anyone that.

## The decision it drives

One: **which companies do I look at first, right now.**

Everything in the email either serves that decision or should not be there.
The reader finishes it by opening two or three links, or by closing the issue
because none of them are worth it. Both are successful outcomes.

## The reader, and the moment

One person. Once a day, most likely on a phone, most likely between other
things. He already knows what the board is and does not need it explained.

He is not reading for pleasure and there is no second reader to impress. The
correct amount of time to spend on this email is under a minute on a quiet
day, and it is quiet most days: the run that prompted this redesign carried
exactly one company.

## What must survive any redesign

These are load-bearing. A design that drops one of them is wrong however it
looks.

1. **The count, first, in the subject.** "1 new AE opening" is the whole
   message on most days. He must be able to decide whether to open the email
   without opening the email.

2. **The transition, not the state.** "was Unknown, now Yes" is the reason the
   company is in the email at all. A design that renders this as a status
   badge loses the thing that made it news. A company that has been hiring for
   six weeks is not in here; that is the point.

3. **The page-scan warning, attached to the company it applies to.** Some
   openings come from a structured job board and some from reading a careers
   page, and the second kind is materially less reliable. This board's entire
   claim is that it says where a number came from. The warning is not a
   disclaimer to be tucked into a footer; it is a property of that specific
   company on that specific day, and it must sit where he cannot act on the
   link without having seen it.

4. **Every role links to the employer's own posting.** He applies on their
   site. A link that goes anywhere else has failed.

5. **One company is the normal case.** The design must not look broken,
   empty, or apologetic when it carries a single entry. It should look the
   same as when it carries eight.

6. **A closing instruction.** The issue is a worklist with a finish line.
   "Close this once you've worked the list" is what makes it a task rather
   than an archive.

## What it must not become

- **A digest.** Adding "here is what else changed" restores the thing this
  replaced: a list nobody works because it is never finished.
- **Silent about provenance.** Dropping the page-scan warning would make the
  email cleaner and would be the single most damaging change available.
- **Chatty.** No greeting, no sign-off, no encouragement. It is a work order.
- **A place for closures.** A company that stopped hiring is a real change and
  is deliberately not here. It is visible on the site. It does not need to
  interrupt a Tuesday.

## How to tell it worked

He opens it, and within about fifteen seconds either has a link open or has
closed the issue. If he has to read it twice to find out how many companies
there are, or clicks a page-scan opening without having noticed the warning,
the design has failed regardless of how it looks.

## Constraints the design inherits

- It renders as a **GitHub issue** and arrives as GitHub's notification email.
  The available vocabulary is markdown: headings, bold, lists, links,
  blockquotes, tables, and emoji. There is no CSS, no custom layout, and the
  email wrapper (Unsubscribe, "view it on GitHub", the mobile-app footer) is
  GitHub's and cannot be removed.
- The body is assembled in `render()` in `scripts/alert.py`. Fields available
  per company today: name, location, sector, category, one-line description,
  previous status, up to five roles with title, location and url, the
  page-scan flag, and the website.
- The subject line is set in `refresh.yml` and is the only thing many days
  will be read at all.
