// ============================================================
// CONFIG.JS — Daksh Grand Earn
// Update the values below before deploying
// ============================================================

const CONFIG = {

    // Your Render backend URL (after deploying on Render)
    API_BASE_URL: "https://earning-bot-b27x.onrender.com",

    // Your Telegram bot username (without @)
    BOT_USERNAME: "Cdotern_bot",

    // Admin Telegram username for support contact (with or without @)
    ADMIN_TELEGRAM: "@cdotern_help",

    // ──────────────────────────────────────────────────────────
    // LOTTERY LOCK
    //
    //   true  → Lottery card visible & active (users can buy tickets)
    //   false → Lottery card shows 🔒 lock animation (coming soon)
    //
    //   Backend switch (/setlottery) controls prize/price/active.
    //   This config switch is the FRONTEND lock — set to false to
    //   hide/lock the lottery without touching the backend at all.
    // ──────────────────────────────────────────────────────────
    LOTTERY_ACTIVE: false,

    // ──────────────────────────────────────────────────────────
    // REFERRAL LOCK
    //
    //   true  → Referral tab active, withdrawal mein 5 referrals required
    //   false → Referral tab 🔒 locked dikhega (coming soon)
    //           Withdrawal mein referral condition "Not Required ✅" ho jaayegi
    // ──────────────────────────────────────────────────────────
    REFERRAL_ACTIVE: true,

    // Monetag Telegram Mini App Rewarded Interstitial Zone ID
    MONETAG_ZONE_ID: "10822310",

    // Paste the exact SDK script URL from your Monetag dashboard
    MONETAG_SDK_URL: "https://libtl.com/sdk.js",

    // ──────────────────────────────────────────────────────────
    // YOUTUBE TASKS LOCK
    //
    //   true  → YouTube tasks unlocked & visible (normal mode)
    //   false → YouTube tasks LOCKED with 🔒 animation overlay
    //
    //   Default: false (locked) — change to true when you have
    //   YT videos ready and want users to start earning from them.
    // ──────────────────────────────────────────────────────────
    YT_TASKS_ACTIVE: false,

    // YouTube video links for Task tab (replace with your own)
    YT_LINKS: {
        yt1: "https://youtu.be/YOUR_VIDEO_1",
        yt2: "https://youtu.be/YOUR_VIDEO_2",
        yt3: "https://youtu.be/YOUR_VIDEO_3"
    },

    // Website links for Task tab (replace with your own)
    WEB_LINKS: {
        web1: "https://shrtslug.biz/8P5ZF",
        web2: "https://shrtslug.biz/8Pd76",
        web3: "https://shrtslug.biz/8Pd7j"
    },

    // Telegram channel links — used by channel join buttons
    CHANNELS: {
        official: "https://t.me/cdoternoffical",
        channel2: "https://t.me/chatdotern",
        channel3: "https://t.me/chatdotern"
    },

    // Partner task links — used by Partner Slot code-verify tasks
    PARTNER_LINKS: {
        partner1: "https://your-partner-link.com"
    },

    // ──────────────────────────────────────────────────────────
    // SPONSOR SLOTS — Sirf yahan badlo, HTML mat chhoona
    //
    //   active : false  →  Slot locked dikhega (🔒 animation)
    //   active : true   →  Slot unlock ho jaayega automatically
    //
    //   type "channel"  →  Join button (link = t.me/...)
    //   type "task"     →  Open link + claim button (link = website)
    //   type "verify"   →  Visit + enter code (admin sets via /settask)
    //
    //   Slot 4 bhi yahan se control hoti hai
    // ──────────────────────────────────────────────────────────
    SPONSORS: {

        slot1: {
            active: false,
            icon:   "💼",
            name:   "Sponsor Slot 1",
            desc:   "Contact admin to advertise here",
            link:   "https://youtube.com/shorts/E2g3GuGeDtw?si=p0KQ0f7C37Tc-n9J",
            reward: 3,
            type:   "channel"
        },

        slot2: {
            active: false,
            icon:   "📢",
            name:   "Sponsor Channel",
            desc:   "Join the channel & earn instantly",
            link:   "https://t.me/Cdotchat",
            reward: 3,
            type:   "channel"
        },

        slot3: {
            active: false,
            icon:   "🌟",
            name:   "Code Verification Partner",
            desc:   "Visit the channel & enter the code to earn",
            link:   "https://t.me/cdoternoffical",
            reward: 4,
            type:   "verify"
        },

        slot4: {
            active: false,
            icon:   "🌐",
            name:   "Sponsor Website Task",
            desc:   "Visit the site & enter code to earn",
            link:   "https://your-sponsor-link.com",
            reward: 4,
            type:   "verify"
        }

    }

};
