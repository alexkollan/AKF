-- FishBridge
--
-- Draws four solid colour cells in the top-left corner of the screen. The fishing engine
-- reads them off the screen and learns a few facts it cannot hear or see otherwise: how
-- many bag slots are free, whether you are in combat, whether a fishing pole is equipped,
-- whether a lure is on and how long the current fishing channel lasts.
--
-- Only plain, non-secret API is used. Every reading is wrapped in pcall and has its own
-- "valid" bit, so if WoW Forever hides one of them the others keep working and the engine
-- simply treats that one as unknown.
--
-- Layout (must match engine/bridge.py). Each cell is CELL_PX x CELL_PX physical pixels,
-- left to right from the top-left corner of the game window:
--
--   cell 0  magic          R=255            G=0                 B=255
--   cell 1  bags           R=free slots     G=total slots       B=heartbeat (counts up)
--   cell 2  state          R=flags*16       G=lure seconds/10   B=channel seconds
--   cell 3  validity       R=valid*8        G=version*16        B=170 (end marker)
--
--   flags: 1 in combat, 2 fishing pole equipped, 4 lure on, 8 channelling
--   valid: 1 bags, 2 combat, 4 pole, 8 lure, 16 channel

local CELL_PX = 4
local VERSION = 1
local UPDATE_INTERVAL = 0.25

local FLAG_COMBAT, FLAG_POLE, FLAG_LURE, FLAG_CHANNEL = 1, 2, 4, 8
local VALID_BAGS, VALID_COMBAT, VALID_POLE, VALID_LURE, VALID_CHANNEL = 1, 2, 4, 8, 16

local WEAPON_CLASS, FISHING_POLE_SUBCLASS = 2, 20 -- Enum.ItemClass.Weapon, ...Fishingpole

local GetFreeSlots = (C_Container and C_Container.GetContainerNumFreeSlots) or GetContainerNumFreeSlots
local GetNumSlots = (C_Container and C_Container.GetContainerNumSlots) or GetContainerNumSlots
local GetItemInfoInstant = (C_Item and C_Item.GetItemInfoInstant) or GetItemInfoInstant

local strip = CreateFrame("Frame", "FishBridgeStrip", UIParent)
strip:SetFrameStrata("TOOLTIP")
strip:SetFrameLevel(9999)
if strip.SetIgnoreParentScale then
  strip:SetIgnoreParentScale(true)
end
if strip.SetIgnoreParentAlpha then
  strip:SetIgnoreParentAlpha(true)
end

local cells = {}
for i = 1, 4 do
  local texture = strip:CreateTexture(nil, "OVERLAY")
  if texture.SetSnapToPixelGrid then
    texture:SetSnapToPixelGrid(false)
    texture:SetTexelSnappingBias(0)
  end
  cells[i] = texture
end

-- One physical pixel in this frame's units. At effective scale s the screen is 768/s
-- units tall, so a pixel is 768 / (screen height in pixels * s) units.
local function layout()
  local _, physicalHeight = GetPhysicalScreenSize()
  local unit = 768 / (physicalHeight * strip:GetEffectiveScale())
  local size = CELL_PX * unit
  strip:ClearAllPoints()
  strip:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
  strip:SetSize(size * 4, size)
  for i, texture in ipairs(cells) do
    texture:ClearAllPoints()
    texture:SetPoint("TOPLEFT", strip, "TOPLEFT", (i - 1) * size, 0)
    texture:SetSize(size, size)
  end
end

local function paint(index, r, g, b)
  cells[index]:SetColorTexture(r / 255, g / 255, b / 255, 1)
end

local function clamp(value)
  value = math.floor(value + 0.5)
  if value < 0 then
    return 0
  elseif value > 255 then
    return 255
  end
  return value
end

-- Readings -----------------------------------------------------------------------------
--
-- Each reader does all its comparing and arithmetic itself and returns only plain values
-- it built: literal booleans and clamped integers. If something it read is a secret value,
-- the error happens inside its pcall, not later in update().

local function readBags()
  local free, total = 0, 0
  for bag = 0, NUM_BAG_SLOTS or 4 do
    local slots = GetNumSlots(bag) or 0
    local open, family = GetFreeSlots(bag)
    -- Fish only go into general-purpose bags; specialty bags report a non-zero family.
    if slots > 0 and (family == nil or family == 0) then
      free = free + (open or 0)
      total = total + slots
    end
  end
  return clamp(free), clamp(total)
end

local inCombat = false

local function readCombat()
  if inCombat or InCombatLockdown() then
    return true
  end
  return false
end

local function isFishingPole(slot)
  local itemID = slot and GetInventoryItemID("player", slot)
  if not itemID then
    return false
  end
  local _, _, _, _, _, classID, subclassID = GetItemInfoInstant(itemID)
  return classID == WEAPON_CLASS and subclassID == FISHING_POLE_SUBCLASS
end

local function readPole()
  -- Main hand on classic-style clients; the fishing tool slot where one exists.
  if isFishingPole(INVSLOT_MAINHAND or 16) or isFishingPole(INVSLOT_FISHINGTOOL) then
    return true
  end
  return false
end

local function readLure()
  local hasEnchant, expirationMs = GetWeaponEnchantInfo()
  if hasEnchant and expirationMs then
    return true, clamp(expirationMs / 1000 / 10)
  end
  return false, 0
end

local function readChannel()
  local name, _, _, startMs, endMs = UnitChannelInfo("player")
  if name and startMs and endMs then
    return true, clamp((endMs - startMs) / 1000)
  end
  return false, 0
end

-- Update -------------------------------------------------------------------------------

local heartbeat = 0

local function update()
  local flags, valid = 0, 0
  local free, total, lureTens, channelSeconds = 0, 0, 0, 0

  local ok, a, b = pcall(readBags)
  if ok then
    free, total = a, b
    valid = valid + VALID_BAGS
  end

  ok, a = pcall(readCombat)
  if ok then
    valid = valid + VALID_COMBAT
    if a then
      flags = flags + FLAG_COMBAT
    end
  end

  ok, a = pcall(readPole)
  if ok then
    valid = valid + VALID_POLE
    if a then
      flags = flags + FLAG_POLE
    end
  end

  ok, a, b = pcall(readLure)
  if ok then
    valid = valid + VALID_LURE
    if a then
      flags = flags + FLAG_LURE
      lureTens = b
    end
  end

  ok, a, b = pcall(readChannel)
  if ok then
    valid = valid + VALID_CHANNEL
    if a then
      flags = flags + FLAG_CHANNEL
      channelSeconds = b
    end
  end

  heartbeat = (heartbeat + 1) % 256

  paint(1, 255, 0, 255)
  paint(2, free, total, heartbeat)
  paint(3, flags * 16, lureTens, channelSeconds)
  paint(4, valid * 8, VERSION * 16, 170)
end

local elapsed = 0
strip:SetScript("OnUpdate", function(_, delta)
  elapsed = elapsed + delta
  if elapsed >= UPDATE_INTERVAL then
    elapsed = 0
    update()
  end
end)

strip:RegisterEvent("PLAYER_LOGIN")
strip:RegisterEvent("DISPLAY_SIZE_CHANGED")
strip:RegisterEvent("UI_SCALE_CHANGED")
strip:RegisterEvent("PLAYER_REGEN_DISABLED")
strip:RegisterEvent("PLAYER_REGEN_ENABLED")
strip:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_REGEN_DISABLED" then
    inCombat = true
  elseif event == "PLAYER_REGEN_ENABLED" then
    inCombat = false
  else
    layout()
  end
  update()
end)

layout()
update()
