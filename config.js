// DAKSH GRAND EARN - MASTER CONFIGURATION ⚙️
const APP_CONFIG = {
    // 1. Daily Ads Settings (0/5 Section)
    daily_ads: {
        total_limit: 5,        // Rozana 5 ads ka limit
        reward_per_ad: 10,     // Har ad par 10 coins
        ad_link: "https://t.me/tads_bot" // Apna Ad link yahan badlein
    },

    // 2. Partnership & Sponsors (3 Telegram Channels)
    sponsors: [
        { 
            name: "Official Partner", 
            username: "@YourPartner1", 
            link: "https://t.me/Link1", 
            reward: 100 
        },
        { 
            name: "Premium Sponsor", 
            username: "@YourPartner2", 
            link: "https://t.me/Link2", 
            reward: 50 
        },
        { 
            name: "Support Channel", 
            username: "@YourPartner3", 
            link: "https://t.me/Link3", 
            reward: 30 
        }
    ],

    // 3. YouTube Tasks (Watch & Verify - 3 Tasks)
    youtube_tasks: [
        { id: 'yt1', link: 'https://youtu.be/Video1', code: 'YT786', reward: 20 },
        { id: 'yt2', link: 'https://youtu.be/Video2', code: 'YT555', reward: 20 },
        { id: 'yt3', link: 'https://youtu.be/Video3', code: 'YT999', reward: 20 }
    ],

    // 4. Website Tasks (Visit & Verify - 3 Tasks)
    website_tasks: [
        { id: 'web1', link: 'https://site1.com', code: 'WEB10', reward: 15 },
        { id: 'web2', link: 'https://site2.com', code: 'WEB20', reward: 15 },
        { id: 'web3', link: 'https://site3.com', code: 'WEB30', reward: 15 }
    ],

    // 5. General Settings
    support_email: "codetearn.help@gmail.com",
    min_withdraw: 1000,
    daily_bonus: 10,
    lock_time_hours: 48 // Task complete karne ke baad ka wait time
};
