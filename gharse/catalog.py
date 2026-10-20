# catalog.py — GharSe's taxonomy: every fixed list the app offers people to choose from.
# Kept apart from app.py so the pages, the Ask board, Learn & Earn and the tests all
# draw from one source, and adding a category is a one-line change.

KINDS = ["Food", "Product", "Service"]

CATEGORIES = {
    "Food": [
        "Daily Meal (Veg)", "Daily Meal (Non-Veg)", "Breakfast", "Snacks & Tiffin",
        "Biryani & Rice", "Curries & Sides", "Sweets & Bakes", "Pickles & Masala",
        "Festival / Bulk Order", "Diet / High-Protein", "Kids Meal", "Elderly Meal",
    ],
    "Product": [
        "Flowers & Garlands", "Tailoring & Stitching", "Saree Work & Embroidery",
        "Handloom", "Handicraft & Decor", "Artificial Jewellery", "Candles & Soaps",
        "Gifts & Hampers", "Knitting & Crochet",
    ],
    "Service": [
        "Alterations", "Ironing & Laundry", "Mehendi", "Beauty at Home",
        "Tuition", "Day-care / Baby-sitting", "Event Cooking", "Packing & Assembly",
        "Petty Home Chores",
    ],
}

CUISINES = [
    "South Indian", "North Indian", "Bengali", "Assamese / North-East", "Kerala",
    "Karnataka", "Andhra", "Tamil", "Hyderabadi", "Maharashtrian", "Gujarati / Jain",
    "Chinese / Indo-Chinese", "Other",
]

DIETS = ["Vegetarian", "Non-Vegetarian", "Egg only", "Jain", "No preference"]
SPICE = ["Mild", "Medium", "Spicy"]

AREAS = [
    "HSR Layout", "Koramangala", "BTM Layout", "Bellandur", "Marathahalli",
    "Whitefield", "Electronic City", "Indiranagar", "Jayanagar", "JP Nagar",
    "Banashankari", "Rajajinagar", "Malleshwaram", "Yelahanka", "Hebbal",
    "CBD / MG Road", "Other",
]

LANGUAGES = ["Kannada", "Hindi", "English", "Tamil", "Telugu", "Malayalam", "Bengali", "Marathi", "Assamese"]

SLOTS = ["Breakfast (7–10 AM)", "Lunch (12–2:30 PM)", "Evening (4–6 PM)", "Dinner (7–9:30 PM)", "Anytime"]
UNITS = ["per meal", "per plate", "per piece", "per kg", "per dozen", "per set", "per hour", "per job"]

DELIVERY_MODES = ["Home delivery", "Community pickup point", "Self pickup"]

ORDER_FLOW = ["Placed", "Accepted", "Preparing", "Ready", "Out for delivery", "Delivered"]
ORDER_STATUSES = ORDER_FLOW + ["Cancelled"]

REQUEST_STATUSES = ["Open", "Assigned", "Completed", "Cancelled"]
BID_STATUSES = ["Offered", "Accepted", "Declined"]

PLAN_PERIODS = [7, 15, 26, 30]

PAYMENT_STATUSES = ["Unpaid", "Claimed", "Paid", "Refund due", "Refunded"]


# ----------------------------------------------------------------------------- ASK
# The neighbourhood Ask board. Anyone can ask for anything a neighbour might do —
# not only petty jobs. Each group lists what people actually ask for; "Something
# else" in every group keeps it open-ended. Grouped so the board can be browsed.
ASK_GROUPS = {
    "Food & Kitchen": [
        "A home-cooked meal", "A special dish on request", "Tiffin for a week or month",
        "Festival or bulk cooking", "Cakes & baking", "Pickles, podi & masalas",
        "Diet or medical meal", "Cooking for a function", "Something else (food)",
    ],
    "Flowers & Pooja": [
        "Jasmine / mallige stringing", "Garlands for a function", "Daily pooja flowers",
        "Rangoli / kolam", "Festival decoration", "Wedding garlands & venue flowers",
        "Something else (flowers)",
    ],
    "Clothes & Tailoring": [
        "Saree designing & embroidery", "Saree fall & pico", "Blouse stitching",
        "Alterations", "Kids' clothes", "Aari / maggam work", "Ironing", "Laundry",
        "Something else (clothes)",
    ],
    "Care at Home": [
        "Babysitting", "Elder care & companionship", "Pet care & dog walking",
        "Patient attendant (non-medical)", "After-school care", "House-sitting",
        "Something else (care)",
    ],
    "Home Help": [
        "Cleaning", "Cooking help", "Plant care & gardening", "Organising & decluttering",
        "Utensil & kitchen deep-clean", "Something else (home)",
    ],
    "Beauty & Wellness": [
        "Mehendi", "Beautician at home", "Hair braiding & styling", "Yoga at home",
        "Something else (beauty)",
    ],
    "Learning & Tuition": [
        "School tuition", "Spoken English", "Music lessons", "Dance lessons",
        "Language lessons", "Computer & phone basics", "Craft classes",
        "Something else (learning)",
    ],
    "Handmade & Gifts": [
        "Custom gift hamper", "Crochet & knitting", "Diyas & candles", "Paintings & art",
        "Return gifts", "Something else (handmade)",
    ],
    "Errands & Events": [
        "Event helper", "Packing & shifting help", "Errands & queues", "Party serving",
        "Something else (errands)",
    ],
}

# Care work happens inside someone's home, often with a child, an elder or a pet.
# Asks in these groups carry a safety note and can only be taken up by verified
# helpers — see ask.py.
ASK_CARE_GROUPS = {"Care at Home"}

# How the two neighbours settle it. Money is one way; it is not the only one.
CONSIDERATIONS = [
    "Fixed price I'll pay",
    "Open to offers",
    "Exchange — I'll do something in return",
    "Free help between neighbours",
]

# ----------------------------------------------------------------------------- LEARN & EARN
JOB_KINDS = ["Part-time", "Full-time", "One-time gig", "Apprenticeship", "Volunteer"]
PAY_UNITS = ["per hour", "per day", "per week", "per month", "per job"]

# Skills a neighbour can teach or learn. Each has a YouTube search that always
# resolves (videos come and go; a search does not 404) — the team adds specific,
# watched-and-vouched-for videos under Learn & Earn.
SKILLS = {
    "Tailoring & stitching":     "basic tailoring stitching tutorial for beginners hindi",
    "Blouse cutting & stitching": "blouse cutting and stitching tutorial",
    "Saree embroidery & aari":   "aari embroidery work tutorial for beginners",
    "Garland & flower making":   "how to make jasmine garland at home",
    "Mehendi":                   "mehndi design tutorial for beginners step by step",
    "Home baking":               "home baking without oven for beginners",
    "Pickle & podi making":      "homemade pickle recipe business",
    "Crochet & knitting":        "crochet for beginners tutorial",
    "Candle & diya making":      "candle making at home tutorial",
    "Rangoli & kolam":           "easy rangoli designs step by step",
    "Spoken English":            "spoken english for beginners",
    "Phone & computer basics":   "smartphone basics for beginners",
    "Baby care basics":          "newborn baby care tips",
    "Elder care basics":         "elderly care at home basic training",
    "First aid":                 "basic first aid training",
    "Food safety & hygiene":     "fssai fostac basic food safety training",
    "Pet grooming & care":       "dog grooming at home for beginners",
    "Beautician basics":         "beautician course for beginners at home",
}
TEACH_MODES = ["At my home", "At the learner's home", "Video call", "Group class nearby"]
SKILL_LEVELS = ["Beginner", "Intermediate", "Advanced"]
