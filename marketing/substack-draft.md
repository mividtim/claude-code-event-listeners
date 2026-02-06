# My AI Pair Programmer Runs the Entire Dev Workflow (And I Mostly Watch)

I've been using Claude Code for a month on a three-year-old production codebase. Not for autocomplete. Not for explaining code. For **running my entire development workflow** — from reading tickets to pushing PRs to planning sprints.

The goal isn't to replace developers. It's to **combine 30+ years of engineering experience with AI's expanded attention span and tireless execution** — a well-oiled code-delivery machine where humans provide judgment and AI provides leverage.

Here's how the setup works.

## The Product: Real Estate on the Blockchain

[Fabrica](https://fabrica.land) tokenizes real estate as NFTs. When you buy a property through Fabrica, you get an onchain title — a legal deed represented as an ERC-721 token. We handle the legal compliance, title insurance, property transfers, and marketplace infrastructure that makes this work in the real world.

This isn't a weekend project. It's a regulated financial product with:
- **Smart contracts** managing millions in real estate assets
- **Legal integrations** with title companies, notaries, and county recorders
- **Marketplace features** — listings, offers, escrow, syndication
- **Compliance workflows** — KYC, accreditation, document notarization
- **Multi-chain support** — Ethereum, Base, with more coming

The complexity is in the intersections: blockchain transactions that trigger legal processes, offchain data that must stay in sync with onchain state, regulatory requirements that vary by jurisdiction.

## The Codebase: Monorepo with Submodules

The architecture reflects this complexity. It's a monorepo with 6 git submodules:

- **NestJS API** — GraphQL, background workers, integrations with Plaid/Stripe/DocuSign/Proof.com
- **React frontend** (Soil) — buyer/seller portal, marketplace, admin tools
- **TheGraph subgraph** — indexes onchain events for fast querying
- **Solidity contracts** — property NFTs, marketplace, escrow, access control
- **Business rules engine** — state machines for complex multi-step workflows
- **Documentation** — user guides, API docs

Each submodule has its own repo, CI, and deployment. A single feature often touches 3-4 of them. Coordinating branches, PRs, and reviews across repos is exactly the kind of tedious work that scales poorly for humans.

**The codebase is three years old. I've been using Claude Code on it for one month.**

In that month, we built all the AI context you're about to read about — the CLAUDE.md instructions, the workflow docs, the CI pipeline, the MCP integrations. It compounded fast.

## Worktrees: One Session Per Feature

I never work on `main`. Every feature gets a **git worktree** — a separate working directory with its own branch. Claude sets this up automatically when I say "work on ENG-2428":

```
fabrica-v3/
├── worktrees/
│   ├── mividtim/ENG-2428-off-ramp-subscription/
│   └── mividtim/ENG-2431-fix-notification-bug/
└── (main checkout, never touched)
```

Each worktree gets its own Claude Code session. The session knows its branch, tracks which PRs belong to it, and cleans up when the work merges. Sessions are named after the ticket (`/rename ENG-2428 Off-Ramp Subscription`) so I can resume with `claude --resume "ENG-2428..."`.

## MCP: Claude Talks to Everything

Claude Code supports **MCP** (Model Context Protocol) — plugins that let Claude interact with external services. My setup includes:

- **Linear** — Claude reads tickets, updates status, creates new issues, estimates complexity
- **Render** — Claude checks deployments, reads logs, manages environments
- **Postgres** — Claude runs queries against staging databases
- **GitHub** — native `gh` CLI for PRs, issues, CI status

When I say "work on ENG-2428", Claude:
1. Fetches the ticket from Linear
2. Creates a worktree
3. Reads the requirements
4. Starts coding

No copy-paste. No context-switching. Claude has direct API access to everything.

## Claude as Project Manager

This surprised me: Claude is now doing a significant chunk of **project management**.

I give high-level instructions like "balance tech debt with feature velocity" or "we need to ship the marketplace by end of month." Claude translates that into:

- **Estimating tickets** — Fibonacci points based on complexity analysis
- **Prioritizing work** — weighing urgency, dependencies, and strategic value
- **Planning cycles** — grouping related tickets, identifying blockers
- **Setting blockers** — marking tickets that depend on other work
- **Balancing the backlog** — ensuring we're not just shipping features while bugs pile up

Before creating a new ticket, Claude searches Linear for duplicates and asks me before proceeding. It assigns complexity estimates (target: ~8 points = 1 day). It tracks velocity so we can see if we're speeding up or slowing down.

I still make the final calls on priorities. But the legwork — reading every ticket, understanding dependencies, estimating scope — Claude does that now.

## The Event-Listeners Plugin: Stop Polling

The biggest friction in AI-assisted dev is **waiting**. Push code → wait for CI. Request review → wait for comments. Start a service → wait for it to boot.

Traditional approach: poll. Sleep 30 seconds, check status, repeat. Burns turns, wastes tokens, feels stupid.

My approach: **event-driven background tasks**.

I built [claude-code-event-listeners](https://github.com/mividtim/claude-code-event-listeners) to solve this. Instead of polling, Claude starts a background process that blocks until something happens:

```
/el:ci-watch my-branch     → Claude sleeps until CI completes
/el:pr-checks 123          → Claude sleeps until all checks resolve
/el:webhook-public 9999    → Claude sleeps until a webhook arrives
/el:file-change api.log    → Claude sleeps until the file changes
```

While waiting, **zero turns are burned**. The OS does the blocking. When the event fires, Claude gets a `<task-notification>`, reads the result, and continues.

This morning Claude:
1. Pushed a PR
2. Started a CI watcher (background)
3. Continued working on tests
4. Got notified when CI failed
5. Read the logs, fixed the issue, pushed again
6. Got notified when CI passed
7. Requested review

All without me touching it.

## CodeRabbit: AI Reviews AI Code

Every PR goes through [CodeRabbit](https://coderabbit.ai), an AI code reviewer. Here's where it gets meta: **Claude responds to CodeRabbit's review comments**.

CodeRabbit posts nitpicks → Claude reads them → Claude fixes the code → Claude replies in the thread → CodeRabbit re-reviews → repeat until approval.

I configured Claude to:
- Wait 2-3 minutes after the first comment (CodeRabbit posts incrementally)
- Batch fixes into single commits (every push = CI run = money)
- Not resolve threads until CodeRabbit accepts the fix
- Track stale approvals (approval before latest push = needs re-review)

Most PRs get approved without me reading the code at all. I review the conversation between the two AIs.

## One Month of Compounding

Here's what we built in the first month:

**Week 1**: Basic CLAUDE.md with coding conventions. Manual everything.

**Week 2**: Worktree workflow. Submodule branching docs. Started using Linear MCP.

**Week 3**: CI pipeline with GitHub Actions. CodeRabbit integration. Push discipline rules.

**Week 4**: Event-listeners plugin. Automated review responses. Project management workflows.

Each piece made the next piece easier to build. Claude helped write its own instructions. The CLAUDE.md file grew from 20 lines to 120. The `.claude/docs/` folder accumulated workflow guides that Claude follows autonomously.

The codebase didn't change much. The **context around Claude** changed everything.

## The Workflow in Practice

A typical morning:

**8:00** — "Start ENG-2597"
Claude reads the ticket, creates worktree, sets up environment.

**8:05** — Claude is coding. I'm drinking coffee.

**8:45** — Claude pushes PR, starts CI watcher.

**9:10** — CI passes. CodeRabbit starts review.

**9:25** — CodeRabbit has comments. Claude addresses them, pushes.

**9:40** — CodeRabbit approves. Claude notifies me.

**9:41** — I skim the PR, type "merge it".

**9:42** — Claude merges, cleans up worktree, updates Linear.

Elapsed time: 1 hour 42 minutes. My time: ~10 minutes of oversight.

## What I Actually Do

- **Architecture decisions** — "Should we use Redis or Postgres for this queue?"
- **Tricky debugging** — When Claude is stuck, I look at the actual code
- **Final approval** — I skim PRs before merging
- **Priority calls** — "Park this, we need to fix the production bug first"
- **Strategic direction** — "We're focusing on marketplace this cycle"

Everything else — the git commands, the dependency updates, the test fixtures, the PR descriptions, the review responses, the ticket estimates, the blocker tracking — Claude handles.

## The Philosophy: Experience + Attention

There's a narrative that AI will replace senior developers first because they're expensive. I think it's backwards.

Junior developers need to *learn* by doing the tedious stuff. Senior developers already know the patterns — they're bottlenecked by *attention*, not knowledge. Every senior dev has a backlog of "I know exactly how to fix this, I just haven't had time."

Our team has 30+ years of experience each. We know how the system should work. We know where the bodies are buried. We know which abstractions are load-bearing and which are accidents of history.

What we *don't* have is infinite attention. We can't hold the entire codebase in our heads while simultaneously:
- Remembering the exact git commands for submodule branching
- Tracking which PR is waiting on which CI run
- Formatting test fixtures correctly
- Writing PR descriptions that satisfy the review bot
- Updating Linear tickets with the right status

Claude can. Claude has **perfect attention** for exactly as long as needed. It never gets bored. It never forgets the style guide. It never fat-fingers a git command because it's thinking about the next task.

So we give Claude the execution. We keep the judgment.

When Claude asks "should I use a database transaction here or is eventual consistency fine?" — that's 30 years of experience answering. When Claude asks "is this test flaky or is it catching a real bug?" — that's judgment from someone who's seen a thousand flaky tests.

The AI makes us faster. The experience makes the AI correct.

## The Meta Bit

Right now, as I write this, Claude is running a **marketing campaign for the event-listeners plugin** on [Moltbook](https://moltbook.com), a social network for AI agents. It's:
- Monitoring engagement on the main post
- Responding to technical questions from other agents
- Scanning for new threads to engage with
- Adapting its polling frequency based on activity

It's been running for hours. The conversation with ODEI (a Neo4j agent builder) has gone 7+ rounds deep into event ordering, race conditions, and state management.

I check in occasionally. Mostly I watch the bots talk to each other.

---

**The plugin**: [github.com/mividtim/claude-code-event-listeners](https://github.com/mividtim/claude-code-event-listeners)

**The campaign**: [moltbook.com/post/fb914efd...](https://www.moltbook.com/post/fb914efd-5cbf-467b-ad67-32a1382af76d)
