# The Lexis server

Everything in Lexis works without this. The deck lives in the browser, the
review loop never touches the network, and two phones can already meet through
a GitHub repository. What a server adds is an account: an email and a password
instead of a personal access token, which is the difference between something
you can hand to somebody else and something only you will ever set up.

It holds cards and reviews. Nothing else. FSRS stays in the browser.

## What it costs

Nothing, at this size. Cloudflare's free tier is 100,000 Worker requests a day
and a 5GB D1 database; a person syncing four times a day uses about 8 of those
requests. The paid tier starts at $5/month and you would need thousands of
users to reach it.

## Setting it up from a phone

You need a Cloudflare account (free, no card) and two secrets in this
repository. There is no terminal step.

1. **Cloudflare → Workers & Pages → D1 → Create database**, named `lexis`.
   Copy the database ID it shows you.

2. Edit `server/wrangler.toml` here on GitHub and paste that ID over
   `REPLACE_WITH_YOUR_D1_DATABASE_ID`.

   While you are in there, set `ALLOWED_ORIGIN` to wherever the app is served
   from — `https://<your-name>.github.io` for GitHub Pages. Any other origin is
   refused, so a page on some other domain cannot quietly use your session.

3. **Cloudflare → My Profile → API Tokens → Create Token**, from the
   *Edit Cloudflare Workers* template. Copy it.

4. In this repository: **Settings → Secrets and variables → Actions → New
   repository secret**, twice:
   - `CLOUDFLARE_API_TOKEN` — the token from step 3
   - `CLOUDFLARE_ACCOUNT_ID` — on the Cloudflare dashboard, right-hand column

5. **Actions → Deploy the Lexis server → Run workflow.**

It prints the address it deployed to, something like
`https://lexis.<your-name>.workers.dev`, and says whether that is the address
the app is built with.

There is no box for it in the app. There is one server and one person using it,
so asking which one on every sign-in was a question with a single possible
answer — and getting it wrong, or losing it on a sign-out, looked exactly like
the whole account being broken. The address is `DEFAULT` in
[`docs/app/where.js`](../docs/app/where.js); if the deploy says it differs from
yours, change it there.

Then open the app and make an account.

The workflow applies `schema.sql` before every deploy. Every statement in it is
`CREATE TABLE IF NOT EXISTS`, so running it again changes nothing.

## Adding your second phone

On the phone that is already signed in: **Settings → Account → Add a phone.**
It gives you a code like `PG87-TPM7`. On the other phone, type the code — there
is no address to enter, because both phones open the same page and the page
knows where the server is. It works once and lasts ten minutes.

This exists because the alternative is typing a real password on a phone
keyboard, and a password short enough to be worth typing twice is a password
not worth having.

## What it does with your data

Stores it, hands it back, and gets out of the way.

- The password is never stored. What is stored is PBKDF2-HMAC-SHA256 at 210,000
  iterations over a per-user random salt, with the iteration count recorded
  alongside so it can be raised later without locking anyone out.
- The session token is never stored either — only its SHA-256, so reading the
  database does not let anyone sign in as anybody.
- Wrong passwords are counted per email and per address, with very different
  limits: eight wrong guesses at one account is somebody guessing, but eight
  from one address is a family or an office, and a building's worth of people
  must not be locked out because one of them keeps mistyping.
- Signing in with an unknown email and signing in with the wrong password give
  the same answer, because telling them apart turns this into a way to find out
  who has an account.
- `GET /api/takeout` gives you everything in one file, and `POST /api/forget-me`
  deletes the account and everything in it. Both are in the app, under Account.
  A service that makes leaving hard is a service that has stopped competing on
  being good.

The email is a name to sign in with. Nothing is ever sent to it, and it is
never checked — so it is not proof of anything and is not treated as proof of
anything.

## The voice

`GET /api/say?text=…&voice=en-US-AvaNeural` answers with an mp3.

It is here for one reason: the page cannot fetch it itself. Microsoft's neural
voices — the ones **Anki TTS package** already puts on the cards — are reached
through an endpoint that checks an `Origin` header of `chrome-extension://…`,
and a browser will not let a page set `Origin`. A Worker will. Without this the
Speak screen falls back to the phone's own `speechSynthesis`, which is where it
started and which sounds nothing like the cards.

Open, with no account. Everything in this app works without one, and a voice
that quietly turns back into the phone's robot for anyone not signed in is the
kind of silent downgrade this project keeps finding and taking out. What keeps
it modest instead: the text is capped at 600 characters, the voice has to be one
of six, and the same sentence twice is served from the cache rather than
generated twice.

That last one comes with a caveat. The Cache API is documented as doing nothing
on a `workers.dev` subdomain, and this is deployed to one — so the cache may be
a saving that only starts once the server has a domain of its own. The response
carries `x-voice-cache: hit` or `miss`, so it is a thing you can check rather
than a thing to believe:

```
curl -sD- -o /dev/null "$SERVER/api/say?text=hello&voice=en-US-AvaNeural" | grep -i x-voice-cache
```

Reverse-engineered rather than documented — `anki/TTS.md` says the same about
the workflow that uses it. If it stops working, change provider rather than
debug it; nothing breaks in the meantime except that the app sounds like a
satnav again.

## Running the tests

```
cd server && npm install && npm test
```

44 checks against a real D1 through Miniflare — the same SQLite and the same
Workers runtime the deployed thing uses. They cover registration, the login
path including the lockouts, session expiry, pairing codes, the merge rules,
and that one account cannot see another's deck.
