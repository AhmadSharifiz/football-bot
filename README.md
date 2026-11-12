# فوتبال آنلاین | Football Online — auto-poster

Posts Persian football news (headline + short summary + source link) to **@footballonline_fa** every 15 minutes, using the bot **@footballonline_fa_bot**. Runs free on GitHub Actions.

## Setup (one time, ~10 min)

1. Create a free account at github.com (skip if you have one).
2. New repository → name it `football-bot` → **Private** → Create.
3. "uploading an existing file" → drag in everything from this zip, **including the `.github` folder** → Commit.
4. Repo **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `TELEGRAM_BOT_TOKEN`
   - Value: the token BotFather sent you in Telegram
5. **Actions** tab → enable workflows if asked.

## Test, then go live

1. Actions → "Post football news" → **Run workflow** (leave "Only test sources" ticked).
   Open the run log: each source shows `items=… football=…`. Sources with 0 items or errors are blocked or have the wrong address — fix or delete them in `sources.yml`.
2. Run workflow again with the box **unticked**. First real run posts nothing; it just marks current news as already seen.
3. From then on it runs every 15 minutes on its own and posts up to 5 new items per run.

## Adjusting

- Sources: `sources.yml` (add a site's homepage; RSS is found automatically, or paste the feed URL under `feeds`).
- Football keywords / excluded sports: top of `bot.py`.
- Posts per run: `MAX_POSTS_PER_RUN` in `.github/workflows/post.yml`.
- Pause: Actions → workflow → "…" → Disable workflow.

If the token ever leaks: BotFather → `/revoke`, then update the secret.
