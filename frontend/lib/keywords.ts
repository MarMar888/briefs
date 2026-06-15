// Per-vertical keyword sets, grouped by category for display on the /keywords page.
//
// SOURCE OF TRUTH: vertical_profiles.py (the scanner runs off the OUTDOOR / CONSTRUCTION
// keyword sets defined there; outdoor lives as OUTDOOR_KEYWORDS in domain_scanner.py).
// This file only renders the lists on the website — keep them in sync when keywords change.

export type KeywordGroup = {
  label: string;
  words: string[];
};

export const KEYWORD_GROUPS: KeywordGroup[] = [
  {
    label: "Snow sports",
    words: ["ski", "skiing", "skier", "skis", "snowboard", "snowboarding", "snowboarder", "snowshoe", "snowshoeing", "snowmobile", "snowmobiling", "snowcat", "nordic", "telemark", "backcountry", "chalet", "alpine", "mogul", "sled", "sledding", "tubing", "biathlon", "iceclimb", "iceclimbing", "snowkite", "snowkiting", "snowpark"],
  },
  {
    label: "Camping / overlanding / RV",
    words: ["camp", "camping", "camper", "campground", "campsite", "glamping", "overland", "overlanding", "basecamp", "bivouac", "rv", "cabin", "cabins", "yurt", "yurts", "tipi", "tipis", "backwoods", "hammock"],
  },
  {
    label: "Hunting",
    words: ["hunt", "hunting", "hunter", "hunters", "bowhunt", "bowhunting", "bowhunter", "waterfowl", "upland", "muzzleloader", "taxidermy", "treestand", "treestands", "camo", "camouflage", "gunclub", "shootingrange", "gunrange", "trapshoot", "trapshooting", "skeet", "deer", "elk", "turkey", "pheasant", "dove", "duck", "goose", "antler", "antlers", "biggame", "trophy", "game", "gamebird", "wildfowl", "varmint", "predatorcalling", "trapper", "trapping", "falconry", "falconer", "rangefinder", "rangefinders"],
  },
  {
    label: "Firearms / shooting",
    words: ["gun", "guns", "rifle", "rifles", "shotgun", "shotguns", "pistol", "pistols", "handgun", "handguns", "revolver", "shoot", "shooting", "shooter", "firearm", "firearms", "gunsmith", "gunsmithing", "archery", "archer", "archeryrange", "crossbow", "bowshop", "bow", "bows", "ammo", "ammunition", "reloading", "decoy", "decoys", "gunshop", "gunstore", "armory", "suppressor", "suppressors", "silencer", "holster", "holsters"],
  },
  {
    label: "Fishing",
    words: ["fish", "fishing", "fisherman", "fishermen", "angler", "angling", "flyfishing", "flyfish", "flyshop", "flyrod", "flytying", "icefishing", "trout", "walleye", "muskie", "musky", "bass", "salmon", "steelhead", "crappie", "bluegill", "catfish", "perch", "pike", "panfish", "tackle", "lure", "lures", "bait", "baits", "wader", "waders", "charter", "charters", "fishingcharter", "reel", "reels", "spey", "tenkara", "nymphing", "bowfishing", "bowfish", "floattrip", "floatfishing"],
  },
  {
    label: "Diving / underwater",
    words: ["dive", "diving", "diver", "divers", "scuba", "snorkel", "snorkeling", "spearfish", "spearfishing", "freedive", "freediving"],
  },
  {
    label: "Paddle sports",
    words: ["kayak", "kayaking", "kayaker", "paddle", "paddling", "paddleboard", "paddleboards", "canoe", "canoeing", "canoeist", "sup", "raft", "rafting", "rafter", "whitewater", "rowboat", "marina", "watercraft", "packraft", "packrafting", "float"],
  },
  {
    label: "Hiking / trail / running",
    words: ["hike", "hiking", "hiker", "hikers", "trail", "trails", "trailhead", "trekking", "trek", "treks", "thru", "backpacker", "trailrun", "trailrunning"],
  },
  {
    label: "Climbing",
    words: ["climb", "climbing", "climber", "climbers", "bouldering", "rappel", "rappelling", "canyoneer", "canyoneering", "crag"],
  },
  {
    label: "Caving",
    words: ["caving", "spelunk", "spelunking"],
  },
  {
    label: "Biking",
    words: ["bike", "bikes", "biking", "biker", "bikers", "cyclist", "cycling", "mountainbike", "mountainbiking", "mtb", "bikepacking", "bikeshop", "cyclocross"],
  },
  {
    label: "ATV / offroad",
    words: ["atv", "utv", "offroad", "fourwheeler", "dirtbike", "dirtbiking"],
  },
  {
    label: "Equestrian",
    words: ["horse", "horses", "horseback", "equestrian", "stable", "stables", "ranch", "ranches", "rodeo", "saddle", "saddles", "trailride", "trailriding"],
  },
  {
    label: "Air / aerial sports",
    words: ["paraglide", "paragliding", "paraglider", "hangglide", "hanggliding", "skydive", "skydiving", "skydiver", "parasail", "parasailing", "gliding", "glider", "soaring", "kiting"],
  },
  {
    label: "Zip / adventure",
    words: ["zipline", "ziplines", "ziplining", "aerial", "ropescourse", "adventure", "adventures", "adventurer", "expedition", "expeditions"],
  },
  {
    label: "Guiding / outfitting",
    words: ["guide", "guides", "guiding", "outfitter", "outfitters", "outfitting"],
  },
  {
    label: "Gear / retail signals",
    words: ["gear", "sporting", "sportinggoods", "sport", "sports", "supply", "supplies", "rental", "rentals", "proshop", "tradingpost", "consignment", "closeout", "liquidation", "demo"],
  },
  {
    label: "Venues / lodging",
    words: ["lodge", "lodges", "lodging", "resort", "resorts", "campground", "campgrounds", "sportsman", "sportsmen", "sportswoman", "sportingclub", "wilderness", "preserve", "retreat", "retreats", "duckclub", "huntingclub", "fishingclub", "marina", "marinas", "outpost"],
  },
  {
    label: "Boating / watersports",
    words: ["boat", "boats", "boating", "boater", "sailboat", "pontoon", "johnboat", "bassboat", "dock", "docks", "pier", "launch", "waterski", "waterskiing", "wakeboard", "wakeboarding", "jetski", "waverunner", "surf", "surfing", "surfer", "surfboard", "windsurfing", "kitesurfing", "kitesurf"],
  },
  {
    label: "Hunting accessories / blinds",
    words: ["groundblind", "huntingblind", "broadhead", "broadheads", "venison", "gameprocessing", "retriever", "spaniel"],
  },
  {
    label: "Survival / bushcraft",
    words: ["survival", "survivalist", "bushcraft", "prepper", "preppers", "knife", "knives", "blade", "blades", "hatchet", "axe", "axes", "tomahawk"],
  },
  {
    label: "Water treatment / hydration",
    words: ["hydration", "hydrate", "canteen", "canteens", "filtration", "purifier", "purifiers", "purification", "potable"],
  },
  {
    label: "Mountain biking extras",
    words: ["singletrack", "enduro", "gravel"],
  },
  {
    label: "Bird watching",
    words: ["birding", "birdwatching", "birder"],
  },
  {
    label: "Exploration / ecotourism",
    words: ["explore", "explorer", "exploration", "excursion", "excursions", "safari", "ecotour", "ecotourism"],
  },
  {
    label: "Target sports",
    words: ["paintball", "airsoft"],
  },
  {
    label: "Broad outdoor",
    words: ["outdoor", "outdoors", "backpack", "backpacking", "mountaineer", "mountaineering", "nature", "naturalist", "wildlife", "wildland", "wildlands", "portage", "mountain", "mountains", "river", "rivers", "lake", "lakes", "forest", "forests", "woods", "woodland"],
  },
];

export const KEYWORD_COUNT = KEYWORD_GROUPS.reduce((sum, g) => sum + g.words.length, 0);

// Mirror of CONSTRUCTION_KEYWORDS in vertical_profiles.py.
export const CONSTRUCTION_KEYWORD_GROUPS: KeywordGroup[] = [
  {
    label: "General / GC / builders",
    words: ["construction", "constructions", "contractor", "contractors", "contracting", "builder", "builders", "building", "build", "homebuilder", "homebuilders", "homebuilding", "designbuild", "generalcontractor", "gc"],
  },
  {
    label: "Roofing / exterior",
    words: ["roofing", "roofer", "roofers", "roof", "siding", "gutters", "gutter", "waterproofing"],
  },
  {
    label: "Concrete / masonry / structural / site",
    words: ["concrete", "masonry", "mason", "masons", "brick", "bricklayer", "framing", "framer", "framers", "foundation", "foundations", "excavation", "excavating", "excavator", "excavators", "earthwork", "grading", "demolition", "demo", "paving", "paver", "pavers", "asphalt", "septic"],
  },
  {
    label: "Finishing trades",
    words: ["drywall", "sheetrock", "plaster", "plastering", "flooring", "floors", "tile", "tiling", "painting", "painter", "painters", "insulation", "windows", "doors", "cabinet", "cabinets", "cabinetry", "decking", "decks", "fencing", "fence", "fences"],
  },
  {
    label: "Mechanical / MEP",
    words: ["plumbing", "plumber", "plumbers", "electrical", "electric", "electrician", "electricians", "hvac", "heating", "cooling", "airconditioning", "mechanical"],
  },
  {
    label: "Remodeling / renovation",
    words: ["remodeling", "remodel", "remodeler", "remodelers", "renovation", "renovations", "renovating"],
  },
  {
    label: "Landscaping / hardscaping / site work",
    words: ["landscaping", "landscaper", "landscapers", "hardscaping", "hardscape", "hardscapes", "sitework"],
  },
];

export const CONSTRUCTION_KEYWORD_COUNT = CONSTRUCTION_KEYWORD_GROUPS.reduce((sum, g) => sum + g.words.length, 0);

export const KEYWORDS_BY_INDUSTRY: Record<string, { groups: KeywordGroup[]; count: number }> = {
  outdoor: { groups: KEYWORD_GROUPS, count: KEYWORD_COUNT },
  construction: { groups: CONSTRUCTION_KEYWORD_GROUPS, count: CONSTRUCTION_KEYWORD_COUNT },
};

export function keywordsFor(industry: string) {
  return KEYWORDS_BY_INDUSTRY[industry] ?? KEYWORDS_BY_INDUSTRY.outdoor;
}
