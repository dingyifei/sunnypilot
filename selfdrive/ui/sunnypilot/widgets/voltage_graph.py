"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time
from collections import deque
from dataclasses import dataclass

import pyray as rl

from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


@dataclass
class VoltageReading:
  """A single voltage reading with timestamp."""
  timestamp: float  # monotonic time
  voltage_mv: int   # voltage in millivolts


class VoltageHistory:
  """
  Records voltage readings after engine-off for visualization.
  Stores readings in a circular buffer until the shutoff limit.
  """
  # Maximum history duration in seconds (30 hours - matches MAX_TIME_OFFROAD_S)
  MAX_HISTORY_DURATION_S = 30 * 60 * 60
  # Sample interval in seconds (record every 30 seconds for long-term monitoring)
  SAMPLE_INTERVAL_S = 30.0

  def __init__(self):
    # Use deque with no maxlen - we'll trim based on time
    self._readings: deque[VoltageReading] = deque()
    self._last_sample_time: float = 0.0
    self._recording_start_time: float | None = None
    self._is_recording: bool = False

  def start_recording(self) -> None:
    """Start recording voltage when going offroad."""
    if not self._is_recording:
      self._readings.clear()
      self._recording_start_time = time.monotonic()
      self._last_sample_time = 0.0
      self._is_recording = True

  def stop_recording(self) -> None:
    """Stop recording when going onroad."""
    self._is_recording = False

  def add_reading(self, voltage_mv: int) -> None:
    """Add a voltage reading if enough time has passed since last sample."""
    if not self._is_recording:
      return

    now = time.monotonic()

    # Only sample at specified interval
    if now - self._last_sample_time < self.SAMPLE_INTERVAL_S:
      return

    self._last_sample_time = now
    self._readings.append(VoltageReading(timestamp=now, voltage_mv=voltage_mv))

    # Trim old readings beyond max duration
    cutoff_time = now - self.MAX_HISTORY_DURATION_S
    while self._readings and self._readings[0].timestamp < cutoff_time:
      self._readings.popleft()

  @property
  def readings(self) -> list[VoltageReading]:
    """Return a copy of all readings."""
    return list(self._readings)

  @property
  def is_recording(self) -> bool:
    return self._is_recording

  @property
  def recording_duration_s(self) -> float:
    """Return how long we've been recording."""
    if self._recording_start_time is None:
      return 0.0
    return time.monotonic() - self._recording_start_time

  def get_voltage_range(self) -> tuple[float, float]:
    """Return (min_voltage_v, max_voltage_v) from readings, or defaults."""
    if not self._readings:
      return (11.5, 13.0)

    voltages = [r.voltage_mv / 1000.0 for r in self._readings]
    min_v = min(voltages)
    max_v = max(voltages)

    # Add some padding and round to nice values
    padding = 0.2
    min_v = max(10.0, min_v - padding)
    max_v = min(15.0, max_v + padding)

    # Ensure at least 1V range
    if max_v - min_v < 1.0:
      mid = (max_v + min_v) / 2
      min_v = mid - 0.5
      max_v = mid + 0.5

    return (min_v, max_v)


class VoltageGraphWidget(Widget):
  """
  A widget that displays a voltage graph showing battery voltage over time
  since the engine was turned off.
  """
  # Colors
  GRAPH_BG_COLOR = rl.Color(30, 30, 30, 255)
  GRAPH_BORDER_COLOR = rl.Color(80, 80, 80, 255)
  GRID_COLOR = rl.Color(60, 60, 60, 255)
  LINE_COLOR = rl.Color(0, 200, 100, 255)  # Green for voltage line
  LINE_COLOR_LOW = rl.Color(255, 100, 50, 255)  # Orange/red for low voltage
  TEXT_COLOR = rl.Color(180, 180, 180, 255)
  CURRENT_VOLTAGE_COLOR = rl.Color(255, 255, 255, 255)

  # Low voltage threshold (11.8V as per VBATT_PAUSE_CHARGING)
  LOW_VOLTAGE_THRESHOLD = 11.8

  # Layout constants
  PADDING = 20
  LABEL_WIDTH = 60
  LABEL_HEIGHT = 30

  def __init__(self, voltage_history: VoltageHistory, height: int = 200):
    super().__init__()
    self._voltage_history = voltage_history
    self._height = height
    self._current_voltage_mv: int = 12000
    self._font = gui_app.font(FontWeight.NORMAL)
    self._small_font = gui_app.font(FontWeight.NORMAL)

  def set_current_voltage(self, voltage_mv: int) -> None:
    """Update the current voltage reading."""
    self._current_voltage_mv = voltage_mv

  def _render(self, rect: rl.Rectangle) -> None:
    # Use provided rect dimensions
    graph_rect = rl.Rectangle(
      rect.x + self.LABEL_WIDTH,
      rect.y + self.PADDING,
      rect.width - self.LABEL_WIDTH - self.PADDING,
      self._height - self.LABEL_HEIGHT - self.PADDING
    )

    # Draw background
    rl.draw_rectangle_rounded(
      rl.Rectangle(rect.x, rect.y, rect.width, self._height),
      0.05, 10, self.GRAPH_BG_COLOR
    )
    rl.draw_rectangle_rounded_lines_ex(
      rl.Rectangle(rect.x, rect.y, rect.width, self._height),
      0.05, 10, 2, self.GRAPH_BORDER_COLOR
    )

    # Get voltage range
    min_v, max_v = self._voltage_history.get_voltage_range()

    # Draw grid lines and Y-axis labels
    self._draw_grid(graph_rect, min_v, max_v)

    # Draw the voltage line
    readings = self._voltage_history.readings
    if len(readings) >= 2:
      self._draw_voltage_line(graph_rect, readings, min_v, max_v)

    # Draw current voltage text
    self._draw_current_voltage(rect)

    # Draw time labels
    self._draw_time_labels(graph_rect)

  def _draw_grid(self, graph_rect: rl.Rectangle, min_v: float, max_v: float) -> None:
    """Draw horizontal grid lines and Y-axis voltage labels."""
    # Draw 4-5 horizontal grid lines
    num_lines = 4
    v_range = max_v - min_v

    for i in range(num_lines + 1):
      y_ratio = i / num_lines
      y = graph_rect.y + graph_rect.height * y_ratio
      voltage = max_v - (v_range * y_ratio)

      # Draw grid line
      rl.draw_line_ex(
        rl.Vector2(graph_rect.x, y),
        rl.Vector2(graph_rect.x + graph_rect.width, y),
        1, self.GRID_COLOR
      )

      # Draw voltage label
      label = f"{voltage:.1f}V"
      text_size = measure_text_cached(self._small_font, label, 28)
      rl.draw_text_ex(
        self._small_font, label,
        rl.Vector2(graph_rect.x - text_size.x - 10, y - text_size.y / 2),
        28, 0, self.TEXT_COLOR
      )

    # Draw low voltage threshold line if in range
    if min_v < self.LOW_VOLTAGE_THRESHOLD < max_v:
      threshold_y = graph_rect.y + graph_rect.height * (1 - (self.LOW_VOLTAGE_THRESHOLD - min_v) / v_range)
      rl.draw_line_ex(
        rl.Vector2(graph_rect.x, threshold_y),
        rl.Vector2(graph_rect.x + graph_rect.width, threshold_y),
        2, self.LINE_COLOR_LOW
      )

  def _draw_voltage_line(self, graph_rect: rl.Rectangle, readings: list[VoltageReading],
                         min_v: float, max_v: float) -> None:
    """Draw the voltage line graph."""
    if len(readings) < 2:
      return

    v_range = max_v - min_v
    time_start = readings[0].timestamp
    time_end = readings[-1].timestamp
    time_range = max(time_end - time_start, 1.0)  # Avoid division by zero

    prev_point = None
    for reading in readings:
      # Calculate x position based on time
      time_ratio = (reading.timestamp - time_start) / time_range
      x = graph_rect.x + graph_rect.width * time_ratio

      # Calculate y position based on voltage
      voltage_v = reading.voltage_mv / 1000.0
      voltage_ratio = (voltage_v - min_v) / v_range
      y = graph_rect.y + graph_rect.height * (1 - voltage_ratio)

      current_point = rl.Vector2(x, y)

      if prev_point is not None:
        # Use color based on voltage level
        color = self.LINE_COLOR if voltage_v >= self.LOW_VOLTAGE_THRESHOLD else self.LINE_COLOR_LOW
        rl.draw_line_ex(prev_point, current_point, 3, color)

      prev_point = current_point

    # Draw a dot at the current position
    if prev_point is not None:
      voltage_v = readings[-1].voltage_mv / 1000.0
      color = self.LINE_COLOR if voltage_v >= self.LOW_VOLTAGE_THRESHOLD else self.LINE_COLOR_LOW
      rl.draw_circle_v(prev_point, 6, color)

  def _draw_current_voltage(self, rect: rl.Rectangle) -> None:
    """Draw the current voltage prominently."""
    voltage_v = self._current_voltage_mv / 1000.0
    voltage_text = f"{voltage_v:.2f}V"
    text_size = measure_text_cached(self._font, voltage_text, 48)

    # Position in top-right corner
    x = rect.x + rect.width - text_size.x - self.PADDING
    y = rect.y + self.PADDING

    # Color based on voltage level
    color = self.CURRENT_VOLTAGE_COLOR if voltage_v >= self.LOW_VOLTAGE_THRESHOLD else self.LINE_COLOR_LOW
    rl.draw_text_ex(self._font, voltage_text, rl.Vector2(x, y), 48, 0, color)

    # Draw "Current:" label above
    label = "Current:"
    label_size = measure_text_cached(self._small_font, label, 28)
    rl.draw_text_ex(
      self._small_font, label,
      rl.Vector2(x + text_size.x - label_size.x, y - label_size.y - 5),
      28, 0, self.TEXT_COLOR
    )

  def _draw_time_labels(self, graph_rect: rl.Rectangle) -> None:
    """Draw time labels on X-axis."""
    readings = self._voltage_history.readings
    if not readings:
      return

    duration_s = self._voltage_history.recording_duration_s

    # Format duration
    if duration_s < 60:
      duration_text = f"{int(duration_s)}s"
    elif duration_s < 3600:
      duration_text = f"{int(duration_s // 60)}m {int(duration_s % 60)}s"
    else:
      hours = int(duration_s // 3600)
      minutes = int((duration_s % 3600) // 60)
      duration_text = f"{hours}h {minutes}m"

    # Draw "0" at start
    start_label = "0"
    rl.draw_text_ex(
      self._small_font, start_label,
      rl.Vector2(graph_rect.x, graph_rect.y + graph_rect.height + 5),
      28, 0, self.TEXT_COLOR
    )

    # Draw duration at end
    duration_size = measure_text_cached(self._small_font, duration_text, 28)
    rl.draw_text_ex(
      self._small_font, duration_text,
      rl.Vector2(graph_rect.x + graph_rect.width - duration_size.x, graph_rect.y + graph_rect.height + 5),
      28, 0, self.TEXT_COLOR
    )

    # Draw "Time since engine off" label in center
    center_label = "Time since engine off"
    center_size = measure_text_cached(self._small_font, center_label, 24)
    rl.draw_text_ex(
      self._small_font, center_label,
      rl.Vector2(graph_rect.x + (graph_rect.width - center_size.x) / 2, graph_rect.y + graph_rect.height + 5),
      24, 0, self.TEXT_COLOR
    )


# Global voltage history instance
voltage_history = VoltageHistory()
