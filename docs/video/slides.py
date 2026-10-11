"""Script and slides for the LLM gateway explainer video (see README.md to build it).

Each slide has a chapter, a title, an HTML body, and a list of narration steps. Elements in the
body marked data-step="n" appear when step n starts; the current step's elements are highlighted.
Narration is written for speech (acronyms spelled the way they should be said).
"""

from pathlib import Path

IMG = (Path(__file__).resolve().parents[1] / "img").as_uri() + "/"

CHAPTERS = ["Intro", "What", "Why", "Before", "How", "First principles", "HLD", "LLD", "Infra", "Recap"]


def card(step, title, text, extra=""):
    return f'<div class="card {extra}" data-step="{step}"><div class="ct">{title}</div><div class="cx">{text}</div></div>'


def image(name, caption=""):
    cap = f'<div class="caption">{caption}</div>' if caption else ""
    return f'<div class="imgwrap"><img src="{IMG}{name}"/></div>{cap}'


def rows(header, items):
    """Two-column comparison table; items are (step, left, right)."""
    h = f'<div class="row head"><div>{header[0]}</div><div>{header[1]}</div></div>'
    body = "".join(
        f'<div class="row" data-step="{s}"><div class="l">{left}</div><div class="r">{right}</div></div>'
        for s, left, right in items
    )
    return f'<div class="table">{h}{body}</div>'


SLIDES = [
    # ---------------------------------------------------------------- Intro
    dict(
        chapter="Intro",
        kind="title",
        title="The LLM Gateway",
        body='<div class="subtitle">What it is, why it exists, and how ours is built</div>'
        '<div class="meta">From first principles to running on AWS</div>',
        steps=[
            "Hi. This video is about a piece of software called an L L M gateway. "
            "We'll start with what it is and why anyone needs one, and how people managed before "
            "gateways existed. Then we'll rebuild the idea from very simple building blocks. "
            "After that, we'll walk through the design of the gateway in this project, first the "
            "big picture, then the details, and finally how it runs on Amazon Web Services. "
            "You don't need to be a programmer. If you know roughly what an app and a server are, "
            "you're all set."
        ],
    ),
    dict(
        chapter="Intro",
        title="The plan",
        body='<div class="agenda">'
        + "".join(
            f'<div class="ag"><span class="num">{i + 1}</span>{t}</div>'
            for i, t in enumerate(
                [
                    "What an LLM gateway is",
                    "Why it is needed: the problems",
                    "How people coped before",
                    "How the gateway solves them",
                    "First principles: the simple building blocks",
                    "High-level design",
                    "Low-level design",
                    "Infrastructure on AWS",
                ]
            )
        )
        + "</div>",
        steps=[
            "Here's the plan. Eight short parts, each building on the one before. "
            "Let's begin with the basics."
        ],
    ),
    # ---------------------------------------------------------------- What
    dict(
        chapter="What",
        title="First: what is an LLM?",
        body="""
<div class="flow">
  <div class="box">Your app</div>
  <div class="arrows">
    <div class="arrow r">prompt + secret API key</div>
    <div class="arrow l">answer</div>
  </div>
  <div class="box accent">AI provider<br/><small>OpenAI · Anthropic</small></div>
</div>
<div data-step="2" class="note">The provider runs the model on its own computers and rents it out over the internet.
An <b>API</b> is the fixed way one program asks another for something. The <b>API key</b> says who to bill.</div>
<div data-step="3" class="tokens"><span>The</span><span>&nbsp;cat</span><span>&nbsp;sat</span><span>&nbsp;on</span><span>&nbsp;the</span><span>&nbsp;mat</span>
<div class="tk">1 token ≈ ¾ of a word · you pay per token, in and out</div></div>
""",
        steps=[
            "First, what is an L L M? It stands for large language model. It's the technology "
            "behind tools like ChatGPT and Claude. You send it text, called a prompt, and it sends "
            "text back.",
            "Companies like OpenAI and Anthropic run these models on their own computers and rent "
            "them out over the internet. Your app talks to them through an A P I, which is simply a "
            "fixed way for one program to ask another program for something. Every request carries "
            "a secret A P I key, which is how the provider knows who to bill.",
            "And billing is by the token. A token is a small chunk of text, roughly three quarters "
            "of a word. You pay for the tokens you send in, and for the tokens you get back.",
        ],
    ),
    dict(
        chapter="What",
        title="So what is an LLM gateway?",
        body="""
<div class="gw">
  <div class="col"><div class="box sm">Support bot</div><div class="box sm">Search</div><div class="box sm">Internal tool</div></div>
  <div class="mid"><div class="line"></div><div class="box accent big">LLM Gateway<br/><small>one front door</small></div><div class="line"></div></div>
  <div class="col"><div class="box sm">OpenAI</div><div class="box sm">Anthropic</div></div>
</div>
<div data-step="2" class="note">Like the <b>front desk</b> of an office building: check ID, sign the visitor book,
send people to the right floor, and find someone else if that person is out.</div>
<div data-step="3" class="code">base_url = "https://<b>gateway</b>.example.com/v1"   <span># the only change an app makes</span></div>
""",
        steps=[
            "So what's a gateway? It's a single front door that sits between your apps and the A I "
            "providers. Instead of every app talking to OpenAI or Anthropic directly, every app talks "
            "to the gateway, and the gateway talks to the providers.",
            "Think of the front desk in an office building. Visitors don't wander straight to whoever "
            "they want to see. They stop at the desk, show an I D, get written in the visitor book, "
            "and get sent to the right floor. If that person is out, the desk can send them to "
            "someone else who can help. The gateway does exactly that job for A I requests.",
            "And importantly, apps barely change. Our gateway speaks the same language as OpenAI's "
            "A P I, so any app built for OpenAI can use the gateway just by changing one web address.",
        ],
    ),
    # ---------------------------------------------------------------- Why
    dict(
        chapter="Why",
        title="Why it is needed: six problems",
        body='<div class="grid3">'
        + card(1, "1 · Keys everywhere", "Every app holds a real provider key. One leak lets anyone spend your money.")
        + card(2, "2 · Unknown cost", "One bill per month. Who spent it? Nobody knows.")
        + card(3, "3 · Outages", "Providers go down, slow down, or say “too many requests”.")
        + card(4, "4 · Runaway usage", "One buggy app in a loop burns everyone’s limits and budget.")
        + card(5, "5 · Different formats", "Each provider has its own API. Switching means rewriting code.")
        + card(6, "6 · No visibility", "When it is slow or broken, there is no single place to look.")
        + "</div>",
        steps=[
            "Why would you want one? Because of six problems that show up as soon as more than one "
            "app uses A I. Problem one: secret keys everywhere. Without a gateway, every app needs a "
            "real provider key. Keys get copied into settings files, laptops, and chat messages. If "
            "one leaks, anyone can spend your money.",
            "Problem two: nobody knows who is spending what. The provider sends one bill. It can't "
            "tell you that the support bot used most of it, or that a test script ran all weekend.",
            "Problem three: providers go down, get slow, or reply with too many requests. Every app "
            "has to deal with that on its own, and most simply fail.",
            "Problem four: runaway usage. One buggy app stuck in a loop can burn through the shared "
            "limits and budget, and take every other app down with it.",
            "Problem five: each provider has its own format. Moving from OpenAI to Anthropic means "
            "rewriting code.",
            "And problem six: when something is slow or broken, there's no single place to look.",
        ],
    ),
    # ---------------------------------------------------------------- Before
    dict(
        chapter="Before",
        title="How people coped before gateways",
        body=rows(
            ("Problem", "The old workaround"),
            [
                (1, "Keys", "A copy of the key in every app’s settings; rotated by hand"),
                (2, "Cost", "Download the monthly bill into a spreadsheet and guess"),
                (3, "Outages", "Every team writes its own retry code; emergency code change to switch provider"),
                (4, "Formats", "Two versions of the same feature, one per provider"),
                (5, "Generic API gateways", "Help with keys and simple limits, but don’t understand tokens, cost or streaming"),
            ],
        )
        + '<div data-step="6" class="callout">The same logic, copied into many places. Copies drift apart.</div>',
        steps=[
            "Before gateways were common, teams patched these problems one app at a time. "
            "For keys, each app kept its own copy of the provider key, often in a settings file, "
            "and keys were rotated by hand, when someone remembered.",
            "Cost was tracked by downloading the provider's monthly bill into a spreadsheet, and "
            "guessing who used what.",
            "For outages, every team wrote its own retry code, each a little different. Switching to "
            "another provider during an outage meant an emergency code change.",
            "Supporting a second provider meant writing, and then maintaining, two versions of the "
            "same feature.",
            "Some companies already had general purpose A P I gateways for their websites. Those help "
            "with keys and simple limits, but they don't understand tokens, cost, or long streaming "
            "answers. So the hardest parts were still left to each app.",
            "The common thread: the same logic was copied into many places, and copies drift apart.",
        ],
    ),
    # ---------------------------------------------------------------- How
    dict(
        chapter="How",
        title="How the gateway solves them",
        body=rows(
            ("Problem", "Gateway answer"),
            [
                (1, "Keys everywhere", "<b>Virtual keys</b>: real keys stay inside; each app gets its own <code>gw-</code> key"),
                (2, "Unknown cost", "<b>Usage ledger</b>: one row per request with key, model, tokens, cost, time"),
                (3, "Outages", "<b>Retries, fallback, circuit breaker</b>"),
                (4, "Runaway usage", "<b>Rate limits and monthly budgets</b> per key"),
                (5, "Different formats", "<b>Translation</b>: apps speak OpenAI; the gateway translates for Anthropic"),
                (6, "No visibility", "<b>Metrics and logs</b> for every request"),
                (7, "Bonus", "<b>Cache</b>: identical questions answered from memory, for free"),
            ],
        ),
        steps=[
            "Here's how the gateway answers each problem. Keys first. The gateway holds the real "
            "provider keys, and nobody else ever sees them. Each app gets its own virtual key, which "
            "starts with the letters G W. You can limit it to certain models, give it a budget, and "
            "cancel it instantly, without touching any other app.",
            "Cost. Every request is written to a ledger: which key, which model, how many tokens, "
            "what it cost, and how long it took. Now the bill can be split per app.",
            "Outages. If a provider fails before it starts answering, the gateway quietly tries "
            "again, or falls back to another provider. And if a provider keeps failing, the gateway "
            "stops sending it traffic for a while.",
            "Runaway usage. Each key gets a speed limit, a number of requests per minute, and a "
            "monthly spending cap.",
            "Formats. Apps always speak OpenAI's format. The gateway translates to and from "
            "Anthropic's format behind the scenes.",
            "Visibility. Every request produces measurements, like how long until the first word "
            "appeared, plus one log line, all in one place.",
            "And as a bonus, identical questions can be answered from a short-term memory, called a "
            "cache, at no cost.",
        ],
    ),
    # ---------------------------------------------------------------- First principles
    dict(
        chapter="First principles",
        title="A request goes in, an answer comes out",
        body="""
<div class="fn">
  <div class="req" data-step="1"><div class="ct">request</div>
    <div class="kv"><span>key</span>gw-7Hq…</div>
    <div class="kv"><span>model</span>"fast"</div>
    <div class="kv"><span>messages</span>[ "Summarise this…" ]</div></div>
  <div class="bigarrow" data-step="1">→</div>
  <div class="box accent big" data-step="1">gateway</div>
  <div class="bigarrow" data-step="1">→</div>
  <div class="req" data-step="1"><div class="ct">answer</div><div class="kv"><span>text</span>"Here is a summary…"</div>
    <div class="kv"><span>tokens</span>120 in · 45 out</div></div>
</div>
<div data-step="2" class="callout">Everything else is a series of checks wrapped around one call to a provider.</div>
""",
        steps=[
            "Now let's rebuild the gateway from first principles. Strip away the jargon and it's "
            "made of a handful of very simple ideas. The first: a request is just a message. Who is "
            "asking, which model they want, and the conversation so far. The gateway is a function. "
            "A message goes in, and an answer comes out.",
            "Everything else the gateway does is a series of checks wrapped around one single call "
            "to a provider.",
        ],
    ),
    dict(
        chapter="First principles",
        title="The checks run like airport security",
        body='<div class="chain">'
        + "".join(
            f'<div class="cp" data-step="1"><div class="n">{i + 1}</div><div class="ct">{t}</div><div class="cx">{x}</div></div>'
            for i, (t, x) in enumerate(
                [
                    ("Show ID", "auth"),
                    ("Open a receipt", "usage"),
                    ("Speed limit", "rate limit"),
                    ("Budget left?", "budget"),
                    ("Already answered?", "cache"),
                    ("Board the plane", "route → provider"),
                ]
            )
        )
        + '</div><div data-step="2" class="callout">Any checkpoint can turn you away, with a clear reason: 401, 429, 402…</div>',
        steps=[
            "The checks are arranged like airport security. Show your I D. Open a receipt, so the "
            "trip gets recorded. Check you're under your speed limit. Check you still have budget. "
            "Check whether we already know the answer. Only then, board the plane, which here means "
            "calling the provider.",
            "Any checkpoint can turn you away, and it always says why, with a standard code, like "
            "four oh one for an unknown key, or four twenty-nine for too many requests.",
        ],
    ),
    dict(
        chapter="First principles",
        title="Six everyday objects",
        body='<div class="grid3">'
        + card(1, "Guest list", "Who may enter. Stores a scrambled fingerprint (a <b>hash</b>) of each key, never the key.")
        + card(2, "Bucket of coins", "60 coins, refills 1 per second. Each request takes a coin. Empty? Wait.")
        + card(3, "Receipt book", "One line per request, so cost can be added up later.")
        + card(4, "Stopwatch", "Provider too slow? Stop waiting.")
        + card(5, "Fuse", "Too many failures in a row: trip, stop calling, test again after 30 s.")
        + card(6, "Notebook", "Remembers recent answers: the cache.")
        + "</div>",
        steps=[
            "Under the hood, the gateway is built from six everyday objects. A guest list decides "
            "who is allowed in. We store a scrambled fingerprint of each key, called a hash, never "
            "the key itself, so even a stolen database doesn't reveal any keys.",
            "A bucket of coins enforces speed limits. Picture a bucket that holds sixty coins and "
            "refills by one coin every second. Each request takes a coin. If the bucket is empty, "
            "you wait.",
            "A receipt book records every request, so cost can be added up later.",
            "A stopwatch enforces timeouts. If the provider takes too long, we stop waiting.",
            "A fuse, like the circuit breaker in your house. Too many failures in a row and it trips, "
            "so we stop calling a broken provider, and test it again after thirty seconds.",
            "And a notebook, the cache, remembers recent answers.",
        ],
    ),
    dict(
        chapter="First principles",
        title="Streaming: a tap, not a bucket",
        body="""
<div class="stream">
  <div class="box">Provider</div>
  <div class="drops"><span>Hel</span><span>lo,</span><span>&nbsp;here</span><span>&nbsp;is</span><span>…</span></div>
  <div class="box accent">Gateway</div>
  <div class="drops"><span>Hel</span><span>lo,</span><span>&nbsp;here</span><span>…</span></div>
  <div class="box">App</div>
</div>
<div data-step="2" class="note">User closes the app halfway → the gateway <b>turns off the tap</b> at the provider,
and <b>still writes the receipt</b>, estimating the tokens used.</div>
""",
        steps=[
            "One more idea: streaming. A I answers are generated word by word, and apps show them as "
            "they arrive, like water from a tap rather than a delivered bucket. So the gateway must "
            "pass each drop through immediately, without collecting the whole answer first.",
            "And if the user closes the app halfway through, the gateway has to turn off the tap at "
            "the provider, so we stop paying, and it still has to write the receipt for what was "
            "used.",
        ],
    ),
    dict(
        chapter="First principles",
        title="The first-word rule",
        body=image("hld-request-lifecycle.png"),
        steps=[
            "Streaming creates one hard rule. Before the first word reaches the user, the gateway can "
            "still retry, or switch providers, because the user hasn't seen anything yet. After the "
            "first word, it can't, because you can't un-say a sentence. So a failure after that "
            "point ends the stream with a clear error message instead. And whichever way a request "
            "ends, the same final step writes the receipt and the measurements.",
        ],
    ),
    # ---------------------------------------------------------------- HLD
    dict(
        chapter="HLD",
        title="High-level design: the big picture",
        body=image("hld-system-context.png"),
        steps=[
            "Now the high-level design, the big picture. On the left are the client apps, and the "
            "operator, meaning the person who runs the gateway and hands out keys. In the middle is "
            "the gateway. On the right are OpenAI and Anthropic. Underneath are its helpers: a store, "
            "which is a small database of keys and usage; a limiter for the speed limits; "
            "Prometheus, which collects measurements; and an optional tracing collector.",
        ],
    ),
    dict(
        chapter="HLD",
        title="Seven stages, nested like Russian dolls",
        body=image("hld-pipeline.png"),
        steps=[
            "Inside, every request passes through seven stages, nested like Russian dolls. Observe "
            "is the outermost, and measures everything. Auth checks the key. Usage writes the ledger "
            "entry. Rate limit and budget enforce the limits. Cache can answer instantly. And route, "
            "in the very middle, is the only stage that actually calls a provider. The order is "
            "fixed in the code on purpose, because order matters. You have to know who someone is "
            "before you can check their limits.",
        ],
    ),
    dict(
        chapter="HLD",
        title="Data and safety",
        body='<div class="grid2">'
        + card(1, "Two tables", "<b>keys</b>: who may call, limits, budget<br/><b>usage</b>: one row per request")
        + card(1, "Short-lived memory", "speed-limit buckets, the cache, breaker state")
        + card(2, "Secrets", "Real provider keys only in the gateway; virtual keys stored as hashes")
        + card(2, "Admin door", "Key management is not reachable from the internet")
        + card(2, "Privacy", "Prompts and answers are never written to logs")
        + card(2, "Network", "Outbound traffic limited to web and name lookups")
        + "</div>",
        steps=[
            "What does it remember? Just two tables: keys, with each key's limits and budget, and "
            "usage, with one row per request. Everything else, like the speed-limit buckets, the "
            "cache, and the circuit breakers, lives briefly in memory.",
            "And how is it kept safe? Real provider keys exist only inside the gateway, and virtual "
            "keys are stored as hashes. The admin door, for creating and cancelling keys, isn't "
            "reachable from the internet. Prompts and answers are never written to logs. And the "
            "network only lets the gateway make the outgoing connections it needs.",
        ],
    ),
    dict(
        chapter="HLD",
        title="Trade-offs, written down",
        body='<div class="grid3">'
        + card(1, "Soft budget", "Two requests at the same instant can both pass. Perfect would need reservations.")
        + card(2, "Cache per key", "Fewer hits, but no one ever sees an answer made for someone else.")
        + card(3, "One copy first", "Simple and correct. A short pause on each update, removed later.")
        + "</div>",
        steps=[
            "Every design involves trade-offs, and ours are written down. The budget is a soft cap. "
            "If two requests arrive at exactly the same moment, both may get through and slightly "
            "overspend. Fixing that perfectly would need a reservation system, which isn't worth it "
            "here.",
            "The cache is kept separate for each key. That lowers the hit rate, but guarantees no one "
            "ever sees an answer that was generated for someone else.",
            "And the first version runs as a single copy. That keeps the data simple, at the cost of "
            "a short pause whenever a new version is deployed. We remove that later.",
        ],
    ),
    # ---------------------------------------------------------------- LLD
    dict(
        chapter="LLD",
        title="Low-level design: the blueprint for the code",
        body="""
<div class="tree">gateway/
  app.py            <span># routes and the list of stages</span>
  stages/           <span># observe · auth · usage · rate_limit · budget · cache · route</span>
  providers/        <span># openai · anthropic</span>
  resilience/       <span># retries · circuit breaker</span>
  store/  limiter/  <span># the database and the speed limits</span>
  streams.py        <span># the stream wrapper</span></div>
<div data-step="2" class="clip"><div class="ct">The clipboard (request context)</div>
<div class="kv"><span>key</span>key_d9b7… (support bot)</div><div class="kv"><span>provider</span>anthropic · attempts 2</div>
<div class="kv"><span>tokens</span>120 in · 45 out</div><div class="kv"><span>status</span>200</div></div>
""",
        steps=[
            "The low-level design is the blueprint for the actual code. It's written in Python, "
            "using a web framework called Fast A P I. Each feature lives in its own file: one per "
            "stage, one per provider, and small files for storage, speed limits, and resilience. "
            "The whole core is about seventeen hundred lines.",
            "Each request gets a context object. Think of it as a clipboard that travels with the "
            "request through every stage. Each stage reads from it and writes to it: whose key it "
            "is, which provider was used, how many attempts it took, the token counts, and the "
            "final result.",
        ],
    ),
    dict(
        chapter="LLD",
        title="The key technique: wrapping the stream",
        body=image("lld-stream-wrapper.png"),
        steps=[
            "The most important technique is the stream wrapper. Every stage that needs to know how "
            "a stream ended wraps it, like adding a layer around a pipe. When the stream ends, for "
            "any reason, the layers clean up from the inside out. The innermost layer closes the "
            "connection to the provider and estimates the usage. Next, the usage layer writes the "
            "ledger row. Last, observe records the measurements. That cleanup is protected, so even "
            "a user hanging up mid-sentence can't interrupt it.",
        ],
    ),
    dict(
        chapter="LLD",
        title="Retries and the circuit breaker",
        body='<div class="split"><div class="rot">'
        '<div class="ct">Retry rotation (max 3 attempts)</div>'
        '<div class="att">1 · primary <span class="x">✗</span></div>'
        '<div class="att">2 · fallback <span class="ok">✓</span></div>'
        '<div class="att dim">3 · primary</div>'
        '<div class="cx">small random pauses between attempts</div></div>'
        f'<div class="imgwrap small" data-step="2"><img src="{IMG}lld-breaker.png"/></div></div>',
        steps=[
            "Retries follow a simple rotation. With two providers, the gateway tries the primary, "
            "then the fallback, then the primary again, at most three attempts, with small random "
            "pauses in between, so that retries from many users don't all land at the same instant.",
            "The circuit breaker has three states. Closed means normal. After five failures in a "
            "row, it opens, and that provider is skipped. After thirty seconds it becomes half open, "
            "and lets exactly one trial request through. If that works, it closes again. If not, it "
            "opens again.",
        ],
    ),
    dict(
        chapter="LLD",
        title="Timers and status codes",
        body='<div class="grid4">'
        + card(1, "3 s", "to connect")
        + card(1, "20 s", "for the first word")
        + card(1, "30 s", "of silence mid-answer")
        + card(1, "5 min", "total, no matter what")
        + "</div>"
        '<div class="codes" data-step="2"><span><b>429</b> slow down</span><span><b>402</b> budget used up</span>'
        "<span><b>502 / 504</b> provider failed / timed out</span><span><b>499</b> user hung up</span></div>",
        steps=[
            "There are four timers. Three seconds to connect. Twenty seconds to get the first word. "
            "Thirty seconds of silence allowed in the middle of an answer. And five minutes in total, "
            "no matter what.",
            "Standard status codes tell apps what happened. Four twenty-nine means slow down. Four "
            "oh two means the budget is used up. Five oh two and five oh four mean the provider "
            "failed or timed out. And four ninety-nine is recorded when the user hangs up.",
        ],
    ),
    dict(
        chapter="LLD",
        title="How we know it works",
        body='<div class="split"><div class="big74" data-step="1">74<small>automated tests</small></div>'
        '<div class="script" data-step="2"><div class="ct">A scriptable fake provider</div>'
        "<code>chunks(1), wait(10)</code><span>goes silent mid-answer</span>"
        "<code>status(503)</code><span>fails, so we retry</span>"
        "<code>chunks(1), drop()</code><span>hangs up halfway</span></div></div>",
        steps=[
            "To trust all of this, there are seventy four automated tests, and they run in about "
            "half a minute.",
            "The cleverest part is a fake provider that can be scripted to be slow, to fail, to go "
            "silent, or to hang up halfway. So every failure path can be tested without spending a "
            "cent, and without real keys.",
        ],
    ),
    # ---------------------------------------------------------------- Infra
    dict(
        chapter="Infra",
        title="Containers and Kubernetes",
        body="""
<div class="k8s">
  <div class="kbox"><div class="ct">Kubernetes</div><div class="cx">starts it · restarts it if it crashes · routes traffic to it</div>
    <div class="ctr">container<br/><small>the gateway + everything it needs</small></div></div>
</div>
""",
        steps=[
            "Now, where does it run? The gateway is packaged as a container, which is a sealed box "
            "holding the program and everything it needs, so it runs the same way everywhere. "
            "Containers are run by Kubernetes, a system that starts them, restarts them if they "
            "crash, and sends traffic to them.",
        ],
    ),
    dict(
        chapter="Infra",
        title="On AWS: EKS Auto Mode",
        body=image("hld-aws-eks.png"),
        steps=[
            "We run Kubernetes on Amazon's managed service, called E K S, in Auto Mode, where Amazon "
            "also manages the servers underneath. Everything sits inside a private network, called a "
            "V P C. Only the load balancer is public. It's the front door from the internet. The "
            "gateway and its storage disk live in private subnets that can't be reached directly. "
            "When the gateway calls OpenAI or Anthropic, it leaves through a one way exit, called "
            "the nat gateway. The container image is kept in E C R, Amazon's image store, and the cluster's "
            "audit logs go to CloudWatch.",
        ],
    ),
    dict(
        chapter="Infra",
        title="Everything is described as code",
        body=rows(
            ("Tool", "What it creates"),
            [
                (1, "Terraform", "The Amazon pieces: network, cluster, permissions, image store"),
                (1, "Kubernetes files", "What runs inside: the gateway, its disk, the load balancer rules"),
            ],
        )
        + '<div data-step="2" class="callout">Recreate it, or tear it down, with a few commands. Always delete the inside first.</div>',
        steps=[
            "All of this is described as code. A tool called Terraform creates the Amazon pieces: the "
            "network, the cluster, the permissions, and the image store. A separate set of "
            "Kubernetes files describes what runs inside the cluster.",
            "Keeping them separate means each tool does one job, and the whole setup can be "
            "recreated, or torn down, with a few commands. When tearing down, the inside goes first, "
            "so nothing is left behind and still costing money.",
        ],
    ),
    dict(
        chapter="Infra",
        title="Growing in two steps",
        body=image("hld-deployment.png"),
        steps=[
            "We grow in two steps. Step one, called K one, runs a single copy of the gateway, with "
            "a small database file on a disk. It's simple and correct, but deploying a new version "
            "means a short pause. Step two, K two, runs several copies behind the load balancer, "
            "moves the shared data into proper database services, Postgres and Redis, and adds "
            "automatic scaling. The gateway's own code doesn't change between the two, and that's "
            "the real test of the design.",
        ],
    ),
    dict(
        chapter="Infra",
        title="Restarting without cutting anyone off",
        body='<div class="steps">'
        + "".join(
            f'<div class="st" data-step="1"><div class="n">{i + 1}</div><div>{t}</div></div>'
            for i, t in enumerate(
                [
                    "Mark itself “not ready”: the load balancer stops sending new users",
                    "Wait a few seconds for that to take effect",
                    "Let every answer in progress finish, up to five minutes",
                    "Write the last receipts, then exit",
                ]
            )
        )
        + "</div>",
        steps=[
            "One last detail shows how the pieces fit together. When Kubernetes replaces a running "
            "copy, the gateway first marks itself as not ready, so the load balancer stops sending it "
            "new users. It waits a few seconds for that to take effect. Then it lets every answer in "
            "progress finish, for up to five minutes, writes the last receipts, and only then exits. "
            "Nobody's answer gets cut off mid-sentence.",
        ],
    ),
    # ---------------------------------------------------------------- Recap
    dict(
        chapter="Recap",
        title="Recap",
        body='<div class="recap">'
        + "".join(
            f'<div class="rc" data-step="{s}">{t}</div>'
            for s, t in [
                (1, "<b>What:</b> one front door between your apps and AI providers"),
                (2, "<b>Why:</b> keys, cost, failures and formats were handled badly, in many places"),
                (3, "<b>How:</b> virtual keys, a usage ledger, limits, retries, a circuit breaker, translation, metrics"),
                (4, "<b>Under the hood:</b> a guest list, a bucket of coins, a receipt book, a stopwatch, a fuse, a notebook"),
                (5, "<b>Where:</b> a container on Kubernetes, on AWS, described entirely as code"),
            ]
        )
        + "</div>",
        steps=[
            "To recap. An L L M gateway is a single front door between your apps and A I providers.",
            "It exists because without it, keys, costs, failures, and formats are each handled badly, "
            "in many different places.",
            "It solves them all in one place, with virtual keys, a usage ledger, limits, retries, a "
            "circuit breaker, translation, and measurements.",
            "Under the hood, it's simple ideas: a guest list, a bucket of coins, a receipt book, a "
            "stopwatch, a fuse, and a notebook, all wrapped around one function call.",
            "And it runs as a container on Kubernetes, on Amazon Web Services, described entirely "
            "as code. Thanks for watching.",
        ],
    ),
]
