// DAKSH GRAND EARN - MASTER CONFIGURATION ⚙️
const APP_CONFIG = {
    // 1. Daily Ads Settings (0/5 Section)
    daily_ads: {
        total_limit: 5,        // Rozana kitne ads (5)
        reward_per_ad: 10,     // Ek ad ke kitne coins
        ad_link: "https://t.me/tads_bot" // Yahan apna Ad link dalo
    },

    // 2. Partnership & Sponsors (Join & Verify)
    sponsors: [
        { 
            name: "Official Partner", 
            username: "@YourPartnerChannel", // @ ke saath channel username
            link: "https://t.me/OfficialChannel", 
            reward: 100 
        },
        { 
            name: "Premium Sponsor", 
            username: "@SponsorChannel", 
            link: "https://t.me/SponsorLink", 
            reward: 50 
        }
    ],

    // 3. YouTube Tasks (Watch & Verify)
    youtube_tasks: [
        { id: 'yt1', link: 'https://youtu.be/Video1', code: 'YT786', reward: 20 },
        { id: 'yt2', link: 'https://youtu.be/Video2', code: 'YT555', reward: 20 },
        { id: 'yt3', link: 'https://youtu.be/Video3', code: 'YT999', reward: 20 }
    ],

    // 4. Website Tasks (Visit & Verify)
    website_tasks: [
        { id: 'web1', link: 'https://site1.com', code: 'WEB10', reward: 15 },
        { id: 'web2', link: 'https://site2.com', code: 'WEB20', reward: 15 },
        { id: 'web3', link: 'https://site3.com', code: 'WEB30', reward: 15 }
    ],

    // 5. General Settings
    support_email: "codetearn.help@gmail.com",
    min_withdraw: 1000,
    daily_bonus: 10,
    lock_time_hours: 48 // Task lock time
};
