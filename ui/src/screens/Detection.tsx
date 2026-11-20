import { useEffect, useState } from "react";
import { Camera, FlaskConical, Pipette } from "lucide-react";

import { RoiEditor } from "@/components/RoiEditor";
import { ScreenShell } from "@/components/ScreenShell";
import {
  Choice,
  Field,
  NumberInput,
  Section,
  SliderInput,
  Toggle,
  differs,
} from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Fusion, HsvRange, Profile } from "@/lib/config";
import { dbToRatio, ratioToDb } from "@/lib/config";
import { patchProfile, useDefaults, useProfile } from "@/lib/engine";
import { sendCommand } from "@/lib/ws";
import { useEngineStore } from "@/store/engineStore";

const FUSIONS: { value: Fusion; label: string }[] = [
  { value: "audio", label: "Sound only" },
  { value: "pixel", label: "Picture only (not available yet)" },
  { value: "audio_or_pixel", label: "Sound or picture (not available yet)" },
  { value: "audio_and_pixel", label: "Sound and picture (not available yet)" },
];

const RED_PRESET: HsvRange[] = [
  { h_min: 0, h_max: 10, s_min: 120, s_max: 255, v_min: 90, v_max: 255 },
  { h_min: 170, h_max: 179, s_min: 120, s_max: 255, v_min: 90, v_max: 255 },
];
const BLUE_PRESET: HsvRange[] = [
  { h_min: 95, h_max: 125, s_min: 80, s_max: 255, v_min: 60, v_max: 255 },
];

export function Detection() {
  const profile = useProfile();
  const defaults = useDefaults()?.profile;

  return (
    <ScreenShell
      title="Detection"
      description="How the bot notices a bite. Sound is what works today and what the live tests were done with."
      actions={<TestCast />}
    >
      {profile && defaults && (
        <>
          <MethodSection profile={profile} defaults={defaults} />
          <ListeningSection profile={profile} defaults={defaults} />
          <SearchAreaSection profile={profile} defaults={defaults} />
          <PictureSection profile={profile} defaults={defaults} />
          <GameWindowSection profile={profile} defaults={defaults} />
        </>
      )}
    </ScreenShell>
  );
}

interface Props {
  profile: Profile;
  defaults: Profile;
}

function TestCast() {
  const status = useEngineStore((store) => store.status);
  const connected = useEngineStore((store) => store.connected);
  return (
    <Button
      variant="outline"
      disabled={!connected || status === "running" || status === "paused"}
      title="Cast once, wait for a bite or the timeout, reel in, then stop. The Dashboard shows what it heard."
      onClick={() => sendCommand({ type: "test_cast" })}
    >
      <FlaskConical />
      Test one cast
    </Button>
  );
}

function MethodSection({ profile, defaults }: Props) {
  const fusion = profile.detection.fusion;
  return (
    <Section
      title="Recognising a bite"
      onReset={() => patchProfile({ detection: { fusion: defaults.detection.fusion } })}
    >
      <Field
        label="Listen or look"
        help="Sound hears the splash and needs nothing on screen. Picture watches the bobber dip, which needs bobber vision."
        defaultText="Sound only"
        modified={fusion !== defaults.detection.fusion}
      >
        <Choice
          label="Detection method"
          value={fusion}
          options={FUSIONS}
          onChange={(next) => patchProfile({ detection: { fusion: next } })}
        />
      </Field>
      {fusion !== "audio" && (
        <p className="py-1 text-sm text-state-paused">
          Picture-based detection is not built yet; a session with this setting will refuse to
          start. Switch back to sound only to fish.
        </p>
      )}
    </Section>
  );
}

function ListeningSection({ profile, defaults }: Props) {
  const audio = profile.detection.audio;
  const d = defaults.detection.audio;
  const devices = useEngineStore((store) => store.audioDevices);

  useEffect(() => {
    if (!audio.process_loopback) {
      sendCommand({ type: "list_audio_devices" });
    }
  }, [audio.process_loopback]);

  return (
    <Section
      title="Listening"
      description="The bot hears a bite when the splash rises far enough above the background sound."
      onReset={() => patchProfile({ detection: { audio: d } })}
    >
      <Field
        label="Hear WoW only"
        help="Capture only WoW's own sound, so Discord, music or videos can never sound like a bite. Turn off only if your Windows does not support it."
        defaultText="on"
        modified={audio.process_loopback !== d.process_loopback}
      >
        <Toggle
          label="Hear WoW only"
          checked={audio.process_loopback}
          onChange={(process_loopback) => patchProfile({ detection: { audio: { process_loopback } } })}
        />
      </Field>
      {!audio.process_loopback && (
        <Field
          label="Output device"
          help="Listen to everything this device plays. Mute other apps while fishing."
          defaultText="your default output"
          modified={audio.device_index !== d.device_index}
        >
          <Choice
            label="Output device"
            value={audio.device_index === null ? "default" : String(audio.device_index)}
            options={[
              { value: "default", label: "Default output" },
              ...devices.map((device) => ({
                value: String(device.index),
                label: device.name.replace(" [Loopback]", ""),
              })),
            ]}
            onChange={(value) =>
              patchProfile({
                detection: { audio: { device_index: value === "default" ? null : Number(value) } },
              })
            }
          />
        </Field>
      )}
      <Field
        label="Sensitivity"
        help="How far above the background a sound must rise to count as a bite. Lower catches quieter splashes but also more false ones; raise it if other sounds trigger hooks."
        defaultText={`+${ratioToDb(d.trigger_ratio).toFixed(1)} dB`}
        modified={differs(audio.trigger_ratio, d.trigger_ratio)}
      >
        <SliderInput
          value={Math.round(ratioToDb(audio.trigger_ratio) * 2) / 2}
          min={4}
          max={20}
          step={0.5}
          format={(db) => `+${db.toFixed(1)} dB`}
          onCommit={(db) =>
            patchProfile({
              detection: { audio: { trigger_ratio: Math.round(dbToRatio(db) * 100) / 100 } },
            })
          }
        />
      </Field>
      <Field
        label="Quietest background"
        help="Silence never counts as quieter than this, so a faint noise after total silence is not mistaken for a splash."
        defaultText={`${d.floor_min_dbfs} dB`}
        modified={audio.floor_min_dbfs !== d.floor_min_dbfs}
      >
        <SliderInput
          value={audio.floor_min_dbfs}
          min={-90}
          max={-30}
          step={1}
          format={(db) => `${db} dB`}
          onCommit={(floor_min_dbfs) => patchProfile({ detection: { audio: { floor_min_dbfs } } })}
        />
      </Field>
      <Field
        label="Pitch range"
        help="Only sound between these frequencies counts. The splash lives here; low rumble and high hiss do not."
        defaultText={`${d.band_hz[0]}–${d.band_hz[1]} Hz`}
        modified={differs(audio.band_hz, d.band_hz)}
      >
        <div className="flex items-center gap-2">
          <NumberInput
            value={audio.band_hz[0]}
            min={0}
            max={24000}
            step={50}
            onCommit={(low) =>
              low !== null && patchProfile({ detection: { audio: { band_hz: [low, audio.band_hz[1]] } } })
            }
          />
          <span className="text-mist">to</span>
          <NumberInput
            value={audio.band_hz[1]}
            min={0}
            max={24000}
            step={50}
            unit="Hz"
            onCommit={(high) =>
              high !== null && patchProfile({ detection: { audio: { band_hz: [audio.band_hz[0], high] } } })
            }
          />
        </div>
      </Field>
      <Field
        label="Background memory"
        help="How many seconds of recent sound the background level is worked out from."
        defaultText={`${d.noise_window_s} s`}
        modified={audio.noise_window_s !== d.noise_window_s}
      >
        <NumberInput
          value={audio.noise_window_s}
          min={0.5}
          max={30}
          step={0.5}
          unit="s"
          onCommit={(noise_window_s) =>
            noise_window_s !== null && patchProfile({ detection: { audio: { noise_window_s } } })
          }
        />
      </Field>
      <Field
        label="One bite per"
        help="After a bite is heard, ignore further splashes for this long. One splash has several bursts."
        defaultText={`${d.min_gap_ms} ms`}
        modified={audio.min_gap_ms !== d.min_gap_ms}
      >
        <NumberInput
          value={audio.min_gap_ms}
          min={50}
          max={5000}
          step={50}
          unit="ms"
          onCommit={(min_gap_ms) =>
            min_gap_ms !== null && patchProfile({ detection: { audio: { min_gap_ms } } })
          }
        />
      </Field>
    </Section>
  );
}

function SearchAreaSection({ profile, defaults }: Props) {
  const shot = useEngineStore((store) => store.clientShot);
  const [picking, setPicking] = useState(false);
  const roi = profile.detection.search_roi;
  const bobber = profile.detection.bobber;
  const preset = !differs(bobber.hsv_ranges, RED_PRESET)
    ? "red"
    : !differs(bobber.hsv_ranges, BLUE_PRESET)
      ? "blue"
      : "custom";

  return (
    <Section
      title="Search area and bobber colour"
      description="Where on screen the bobber lands, and what colour its feather is. Used by mode B and picture detection, which are not built yet; sound detection ignores both."
      onReset={() =>
        patchProfile({
          detection: {
            search_roi: defaults.detection.search_roi,
            bobber: { hsv_ranges: defaults.detection.bobber.hsv_ranges },
          },
        })
      }
    >
      <div className="flex flex-wrap items-center gap-2 py-2">
        <Button variant="outline" onClick={() => sendCommand({ type: "screenshot_client" })}>
          <Camera />
          {shot ? "Take a new snapshot" : "Take a snapshot of WoW"}
        </Button>
        {shot && (
          <Button
            variant={picking ? "default" : "outline"}
            onClick={() => setPicking((now) => !now)}
            aria-pressed={picking}
          >
            <Pipette />
            {picking ? "Click the bobber's feather" : "Pick the bobber colour"}
          </Button>
        )}
        <span className="ml-auto text-sm text-mist tabular-nums">
          {Math.round(roi.w * 100)}% × {Math.round(roi.h * 100)}% of the window
        </span>
      </div>
      {shot ? (
        <RoiEditor
          pngBase64={shot.pngBase64}
          roi={roi}
          onRoi={(search_roi) => patchProfile({ detection: { search_roi } })}
          onPick={
            picking
              ? (range) => {
                  setPicking(false);
                  patchProfile({ detection: { bobber: { hsv_ranges: [range] } } });
                }
              : undefined
          }
        />
      ) : (
        <p className="rounded-lg border border-dashed border-shoal p-5 text-sm text-mist">
          Take a snapshot with WoW open, then drag across it to set the search area.
        </p>
      )}
      <Field
        label="Feather colour"
        help="Red for most water, blue for dark water at night, or the colour you picked with the eyedropper."
        defaultText="Red"
        modified={preset !== "red"}
      >
        <div className="flex items-center gap-3">
          <Choice
            label="Feather colour"
            value={preset}
            options={[
              { value: "red", label: "Red feather" },
              { value: "blue", label: "Blue feather" },
              { value: "custom", label: "Picked colour" },
            ]}
            onChange={(next) => {
              if (next === "red") {
                patchProfile({ detection: { bobber: { hsv_ranges: RED_PRESET } } });
              } else if (next === "blue") {
                patchProfile({ detection: { bobber: { hsv_ranges: BLUE_PRESET } } });
              } else {
                setPicking(true);
              }
            }}
          />
          <span className="flex gap-1">
            {bobber.hsv_ranges.map((range, index) => (
              <span
                key={index}
                className="size-6 rounded border border-shoal"
                title={`Hue ${range.h_min}–${range.h_max}`}
                style={{ background: `hsl(${((range.h_min + range.h_max) / 2) * 2} 80% 50%)` }}
              />
            ))}
          </span>
        </div>
      </Field>
    </Section>
  );
}

function GameWindowSection({ profile, defaults }: Props) {
  const c = profile.capture;
  const d = defaults.capture;
  return (
    <Section
      title="Game window"
      description="How the bot finds WoW and how fast it watches it."
      onReset={() => patchProfile({ capture: d })}
    >
      <Field
        label="Game program"
        help="The WoW executable. WoW Forever runs as Wow.exe."
        defaultText={d.process_name}
        modified={c.process_name !== d.process_name}
      >
        <Input
          key={c.process_name}
          className="w-56"
          defaultValue={c.process_name}
          onBlur={(event) => {
            const value = event.target.value.trim();
            if (value && value !== c.process_name) {
              patchProfile({ capture: { process_name: value } });
            }
          }}
        />
      </Field>
      <Field
        label="Window title contains"
        help="Only needed when more than one WoW window is open: pick the one whose title contains this."
        defaultText="(empty)"
        modified={c.window_title_contains !== d.window_title_contains}
      >
        <Input
          key={c.window_title_contains}
          className="w-56"
          defaultValue={c.window_title_contains}
          placeholder="any WoW window"
          onBlur={(event) => {
            if (event.target.value !== c.window_title_contains) {
              patchProfile({ capture: { window_title_contains: event.target.value } });
            }
          }}
        />
      </Field>
      <Field
        label="Screen capture"
        help="DirectX capture is fast; the fallback works everywhere. Automatic uses DirectX and falls back when it fails."
        defaultText="Automatic"
        modified={c.backend !== d.backend}
      >
        <Choice
          label="Screen capture"
          value={c.backend}
          options={[
            { value: "auto", label: "Automatic" },
            { value: "dxcam", label: "DirectX only" },
            { value: "mss", label: "Fallback only" },
          ]}
          onChange={(backend) => patchProfile({ capture: { backend } })}
        />
      </Field>
      <Field
        label="Frames per second"
        help="How often the screen is captured. Only matters for picture detection."
        defaultText={String(d.target_fps)}
        modified={c.target_fps !== d.target_fps}
      >
        <NumberInput
          value={c.target_fps}
          min={10}
          max={240}
          unit="fps"
          onCommit={(target_fps) => target_fps !== null && patchProfile({ capture: { target_fps } })}
        />
      </Field>
    </Section>
  );
}

function PictureSection({ profile, defaults }: Props) {
  const pixel = profile.detection.pixel;
  const d = defaults.detection.pixel;
  const bobber = profile.detection.bobber;
  const db = defaults.detection.bobber;
  return (
    <Section
      title="Picture detection"
      description="Thresholds for spotting the bobber and its dip. Not used until bobber vision is built."
      onReset={() =>
        patchProfile({
          detection: {
            pixel: d,
            bobber: { min_confidence: db.min_confidence, min_blob_area: db.min_blob_area, max_blob_area: db.max_blob_area },
          },
        })
      }
    >
      <Field
        label="Bobber confidence"
        help="How sure the bot must be that it found the bobber before it watches it."
        defaultText={`${Math.round(db.min_confidence * 100)}%`}
        modified={bobber.min_confidence !== db.min_confidence}
      >
        <SliderInput
          value={bobber.min_confidence}
          min={0}
          max={1}
          step={0.05}
          format={(value) => `${Math.round(value * 100)}%`}
          onCommit={(min_confidence) => patchProfile({ detection: { bobber: { min_confidence } } })}
        />
      </Field>
      <Field
        label="Feather size"
        help="Smallest and largest coloured patch, in pixels, that can be the bobber's feather."
        defaultText={`${db.min_blob_area}–${db.max_blob_area} px`}
        modified={bobber.min_blob_area !== db.min_blob_area || bobber.max_blob_area !== db.max_blob_area}
      >
        <div className="flex items-center gap-2">
          <NumberInput
            value={bobber.min_blob_area}
            min={1}
            max={10000}
            onCommit={(min_blob_area) =>
              min_blob_area !== null && patchProfile({ detection: { bobber: { min_blob_area } } })
            }
          />
          <span className="text-mist">to</span>
          <NumberInput
            value={bobber.max_blob_area}
            min={10}
            max={100000}
            unit="px"
            onCommit={(max_blob_area) =>
              max_blob_area !== null && patchProfile({ detection: { bobber: { max_blob_area } } })
            }
          />
        </div>
      </Field>
      <Field
        label="Watch box"
        help="Side of the square around the bobber that is watched for the dip."
        defaultText={`${d.bobber_roi_px} px`}
        modified={pixel.bobber_roi_px !== d.bobber_roi_px}
      >
        <NumberInput
          value={pixel.bobber_roi_px}
          min={20}
          max={400}
          unit="px"
          onCommit={(bobber_roi_px) =>
            bobber_roi_px !== null && patchProfile({ detection: { pixel: { bobber_roi_px } } })
          }
        />
      </Field>
      <Field
        label="Dip depth"
        help="How far down the bobber must move to count as a bite."
        defaultText={`${d.centroid_drop_px} px`}
        modified={pixel.centroid_drop_px !== d.centroid_drop_px}
      >
        <SliderInput
          value={pixel.centroid_drop_px}
          min={1}
          max={30}
          step={0.5}
          format={(value) => `${value} px`}
          onCommit={(centroid_drop_px) => patchProfile({ detection: { pixel: { centroid_drop_px } } })}
        />
      </Field>
      <Field
        label="Disappears below"
        help="A bite also counts when the visible feather shrinks to this share of its size."
        defaultText={`${Math.round(d.area_drop_ratio * 100)}%`}
        modified={pixel.area_drop_ratio !== d.area_drop_ratio}
      >
        <SliderInput
          value={pixel.area_drop_ratio}
          min={0.1}
          max={0.95}
          step={0.05}
          format={(value) => `${Math.round(value * 100)}%`}
          onCommit={(area_drop_ratio) => patchProfile({ detection: { pixel: { area_drop_ratio } } })}
        />
      </Field>
      <Field
        label="Splash movement"
        help="How much the picture around the bobber must change between frames to count as a splash."
        defaultText={String(d.frame_diff_threshold)}
        modified={pixel.frame_diff_threshold !== d.frame_diff_threshold}
      >
        <SliderInput
          value={pixel.frame_diff_threshold}
          min={0}
          max={100}
          step={1}
          format={(value) => String(value)}
          onCommit={(frame_diff_threshold) =>
            patchProfile({ detection: { pixel: { frame_diff_threshold } } })
          }
        />
      </Field>
      <Field
        label="Frames in a row"
        help="The dip has to show in this many consecutive frames, so one flicker is not a bite."
        defaultText={String(d.consecutive_frames)}
        modified={pixel.consecutive_frames !== d.consecutive_frames}
      >
        <NumberInput
          value={pixel.consecutive_frames}
          min={1}
          max={10}
          onCommit={(consecutive_frames) =>
            consecutive_frames !== null && patchProfile({ detection: { pixel: { consecutive_frames } } })
          }
        />
      </Field>
    </Section>
  );
}
