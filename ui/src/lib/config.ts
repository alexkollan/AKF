/**
 * TypeScript view of `engine/config.py`. The engine validates every change, so these types
 * are for editing comfort, not enforcement; keep them in step with the pydantic models.
 */

export type Mode = "A" | "B" | "C";
export type Fusion = "audio" | "pixel" | "audio_or_pixel" | "audio_and_pixel";

export interface HsvRange {
  h_min: number;
  h_max: number;
  s_min: number;
  s_max: number;
  v_min: number;
  v_max: number;
}

export interface Lure {
  name: string;
  key: string;
  duration_min: number;
  cast_time_s: number;
  priority: number;
  enabled: boolean;
}

export interface RoiFraction {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Profile {
  name: string;
  notes: string;
  mode: Mode;
  keys: { fish_key: string; cast_key: string; interact_key: string };
  mouse: {
    shift_right_click: boolean;
    restore_cursor: boolean;
    move_steps: number;
    click_hold_ms: number;
  };
  capture: {
    backend: "dxcam" | "mss" | "auto";
    target_fps: number;
    process_name: string;
    window_title_contains: string;
  };
  timing: {
    land_delay_ms: number;
    max_wait_ms: number;
    hook_delay_ms: number;
    post_hook_delay_ms: number;
    between_casts_ms: number;
    jitter_ms: number;
  };
  detection: {
    fusion: Fusion;
    search_roi: RoiFraction;
    audio: {
      process_loopback: boolean;
      device_index: number | null;
      chunk_ms: number;
      noise_window_s: number;
      trigger_ratio: number;
      floor_min_dbfs: number;
      min_gap_ms: number;
      use_template: boolean;
      template_path: string | null;
      band_hz: [number, number];
    };
    pixel: {
      bobber_roi_px: number;
      centroid_drop_px: number;
      area_drop_ratio: number;
      frame_diff_threshold: number;
      consecutive_frames: number;
    };
    bobber: {
      hsv_ranges: HsvRange[];
      min_blob_area: number;
      max_blob_area: number;
      min_confidence: number;
      template_paths: string[];
      prefer_center: boolean;
    };
  };
  lures: { auto_apply: boolean; refresh_margin_s: number; lures: Lure[] };
  stop: {
    max_duration_min: number | null;
    max_catches: number | null;
    consecutive_misses: number;
    pause_on_focus_loss: boolean;
    stop_on_bags_full: boolean;
    pause_on_combat: boolean;
  };
  fingerprint: {
    client_width: number;
    client_height: number;
    ui_scale: number;
    monitor_name: string;
  };
  calibrated_at: string | null;
}

export interface Settings {
  active_profile: string;
  server_host: string;
  server_port: number;
  hotkeys: { start_stop: string; pause: string; emergency_stop: string };
  notifications: { windows_toast: boolean; webhook_url: string | null };
  window: {
    monitor_index: number;
    x: number | null;
    y: number | null;
    width: number;
    height: number;
    always_on_top: boolean;
  };
  dry_run: boolean;
}

/** A partial, nested patch: what `set_config` / `set_settings` deep-merge. */
export type Patch<T> = { [K in keyof T]?: T[K] extends object[] ? T[K] : T[K] extends object ? Patch<T[K]> : T[K] };

/** Ratio <-> decibels, for showing `trigger_ratio` the way the detector log does. */
export const ratioToDb = (ratio: number): number => 20 * Math.log10(ratio);
export const dbToRatio = (db: number): number => 10 ** (db / 20);
