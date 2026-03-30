// ============================================================
// CONFIG.JS — Daksh Grand Earn
// Update the values below before deploying
// ============================================================

const CONFIG = {

    // Your Render backend URL (after deploying on Render)
    API_BASE_URL: "https://earning-bot-b27x.onrender.com",

    // Your Telegram bot username (without @)
    BOT_USERNAME: "Cdotern_bot",

    // Adsgram Block ID — get it from adsgram.ai after registering
    ADSGRAM_BLOCK_ID: "YOUR_ADSGRAM_BLOCK_ID",

    // YouTube video links for Task tab (replace with your own)
    YT_LINKS: {
        yt1: "https://youtu.be/YOUR_VIDEO_1",
        yt2: "https://youtu.be/YOUR_VIDEO_2",
        yt3: "https://youtu.be/YOUR_VIDEO_3"
    },

    // Website links for Task tab (replace with your own)
    WEB_LINKS: {
        web1: "https://your-website-1.com",
        web2: "https://your-website-2.com",
        web3: "https://your-website-3.com"
    },

    // Telegram channel links — used by channel join buttons
    CHANNELS: {
        official: "https://t.me/cdoternoffical",
        channel2: "https://t.me/Channel2",
        channel3: "https://t.me/Channel3"
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
    //   type "task"     →  Open link + code verify (link = website)
    // ──────────────────────────────────────────────────────────
    SPONSORS: {

        slot1: {
            active: false,
            icon:   "💼",
            name:   "Sponsor Slot 1",
            desc:   "Contact admin to advertise here",
            link:   "",
            reward: 10,
            type:   "channel"
        },

        slot2: {
            active: false,
            icon:   "📢",
            name:   "Sponsor Channel",
            desc:   "Join the channel & earn instantly",
            link:   "https://t.me/YOUR_SPONSOR_CHANNEL",
            reward: 10,
            type:   "channel"
        },

        slot3: {
            active: false,
            icon:   "🌟",
            name:   "Partner Task",
            desc:   "Complete the task & enter code to earn",
            link:   "https://your-partner-link.com",
            reward: 15,
            type:   "task"
        }

    }

};
