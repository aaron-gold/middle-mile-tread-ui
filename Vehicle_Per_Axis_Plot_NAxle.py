# ============================================================
# N-AXLE SYSTEM DIAGRAM                              2026-09-08
# ------------------------------------------------------------
# WHY THIS FILE EXISTS
#
# The system engineer's `Vehicle_Per_Axis_Plot.py` builds its figure as
#
#     fig, axes = plt.subplots(11, 1, ...)
#
# and draws its axle tracks into axes[9] and axes[10], each guarded on a SECOND
# gate-1 pulse:
#
#     if len(g1_rises) > 1 and len(g2_rises) > 1 and len(g3_rises) > 1:
#
# so the grid is fixed at two axles. `Scan_Output_Classified/system_pulse_diagram.png`
# is written by that same code, which is why a 3-axle scan ships a 2-axle picture: on
# 2026-09-07T14-09-47 the trigger log carries three complete gate1/gate2/gate3 sets
# and the saved PNG shows two.
#
# WHAT THIS FILE IS
#
# The same diagram with the axle block generalised. Every row above the axle tracks -
# the five hardware lanes and the four driven outputs - is his, unchanged: the same
# events, the same order, the same colours, the same labels, the same y-limits, the
# same 2.5 s tread window and the same +/-0.05 s OCR marks. What changed is only that
# the grid is 9 + N rows instead of 11, and that the axle block is a loop over N
# instead of two copies of the same fifteen lines.
#
# It is NOT a correction of his figure and it does not replace it. The viewer shows
# his saved PNG for every 1- and 2-axle scan exactly as before; this is drawn only
# when a scan resolves three axles or more, where his grid has no row to put them in.
#
# HOW IT IS LOADED
#
# Through the same path his script is: `load_engineer_plot()` cuts the source at the
# execution marker, exec's it into a private module, injects `os` / `pd` / `plt` /
# `np` as free globals, and calls the function with a host object carrying
# `triggers_file` and `output_dir`. `plt` is the viewer's shim, so `subplots` draws on
# the tab's canvas and `savefig` is a no-op - nothing is ever written into a scan
# folder. Two optional attributes are read off the host when they are there:
#
#     self.n_axles       how many axles the SPLIT resolved. A row is drawn for each,
#                        so the figure and the banner can never disagree. A row the
#                        trigger log cannot support says so on the row itself.
#     self.axle_source   where that number came from, for the subtitle.
#
# WHAT THE AXLE ROWS ARE, unchanged from his model: gate edges plus a fixed 2.5 s
# tread window. They are a model of the firmware, not a measurement, and carry no
# tolerance.
# ============================================================


def generate_n_axle_vehicle_diagram(self):
    """The engineer's system diagram, with one sliced-data row per axle."""
    df = pd.read_csv(self.triggers_file)
    usec_col = 'Host_Timestamp_usec' if 'Host_Timestamp_usec' in df.columns else 'Host'
    min_host = df[usec_col].min()

    # his rule, unchanged: a Host counter above 1e8 is microseconds
    time_scale = 1e6 if min_host > 1e8 else 1e3
    df['rel_sec'] = (df[usec_col] - min_host) / time_scale
    df_sorted = df.sort_values('rel_sec').reset_index(drop=True)
    max_sec = df_sorted['rel_sec'].max() + 1.0

    def edges(event, rising):
        return df_sorted[(df_sorted['Event'] == event)
                         & (df_sorted['Rising_Edge'] == rising)]['rel_sec'].tolist()

    g1_rises, g1_falls = edges('gate1', 1), edges('gate1', 0)
    g2_rises = edges('gate2', 1)
    g3_rises, g3_falls = edges('gate3', 1), edges('gate3', 0)

    CAM_DURATION = 2.5
    laser_pulses_df = df_sorted[
        df_sorted['Event'] == 'TREAD_OUTPUT_LASER_MASTER_ENABLE'].sort_values('rel_sec')

    # How many axle rows the TRIGGER LOG can actually fill. Every one of his five
    # lookups is indexed with the same k, so the answer is the shortest of them.
    n_drawable = min(len(g1_rises), len(g1_falls), len(g2_rises),
                     len(g3_rises), len(g3_falls))
    # How many the SPLIT resolved, when the viewer told us. The figure follows that
    # number so it cannot show fewer axles than the banner claims; a row with no gate
    # data behind it is drawn empty and labelled, never silently dropped.
    n_declared = int(getattr(self, 'n_axles', 0) or 0)
    n_axles = max(n_declared, n_drawable, 1)

    def make_pulsed_laser_step_fixed(laser_df, win_start, win_end, max_t, pad_end=0.3):
        sub_laser = laser_df[(laser_df['rel_sec'] >= win_start - 0.1)
                             & (laser_df['rel_sec'] <= win_end + pad_end)]
        t_pts = [0.0]
        v_pts = [0]
        curr = 0
        for _, row in sub_laser.iterrows():
            t = row['rel_sec']
            edge = row['Rising_Edge']
            t_pts.extend([t, t])
            v_pts.extend([curr, edge])
            curr = edge
        t_pts.append(max_t)
        v_pts.append(curr)
        return t_pts, v_pts

    def make_step(windows_list, max_t):
        t_pts = [0.0]
        v_pts = [0]
        for start, end in windows_list:
            t_pts.extend([start, start, end, end])
            v_pts.extend([0, 1, 1, 0])
        t_pts.append(max_t)
        v_pts.append(0)
        return t_pts, v_pts

    def draw_signal(ax, event_names, color, label):
        """One high/low lane, exactly as his loop builds it."""
        sub_df = df_sorted[df_sorted['Event'].isin(event_names)].sort_values('rel_sec')
        t_pts = [0.0]
        v_pts = [0]
        curr = 0
        for _, row in sub_df.iterrows():
            t = row['rel_sec']
            edge = row['Rising_Edge']
            t_pts.extend([t, t])
            v_pts.extend([curr, edge])
            curr = edge
        t_pts.append(max_sec)
        v_pts.append(curr)
        ax.step(t_pts, v_pts, where='post', color=color, linewidth=2)
        style_lane(ax, label, color)

    def style_lane(ax, label, color):
        ax.set_ylim(-0.2, 1.3)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(['0', '1'], fontsize=9, fontweight='bold')
        ax.set_ylabel(label, fontsize=8.5, fontweight='bold', rotation=0,
                      labelpad=120, ha='left', color=color)
        ax.grid(True, linestyle='--', alpha=0.4)

    n_rows = 9 + n_axles
    fig, axes = plt.subplots(n_rows, 1, figsize=(15, 3.0 + 1.3 * n_rows), sharex=True)

    # 1. Hardware gate signals - his five, in his order
    hardware_signals = [
        ("Input 10 (Entry)", ["VEHICLE_ENTER"], "navy"),
        ("Gate 1 Signal", ["gate1"], "crimson"),
        ("Gate 2 Signal", ["gate2"], "darkorange"),
        ("Gate 3 Signal", ["gate3"], "goldenrod"),
        ("Input 11 (Exit)", ["VEHICLE_EXIT"], "darkred"),
    ]
    for idx, (label, event_names, color) in enumerate(hardware_signals):
        draw_signal(axes[idx], event_names, color, label)

    # 2. Global actuators - his four, in his order
    global_actuators = [
        ("Global OCR Right Strobe\n(Output 1)",
         ["OUTPUT_OCR_AND_FLASH_RIGHT_TRIGGER"], "blue", 5),
        ("Global OCR Left Strobe\n(Output 2)",
         ["OUTPUT_OCR_AND_FLASH_LEFT_TRIGGER"], "teal", 6),
        ("Global Tread Exposure\n(Output 10)",
         ["TREAD_OUTPUT_FLASH_CAMERA_PWM"], "green", 7),
        ("Global Laser Enable\n(Output 20)",
         ["TREAD_OUTPUT_LASER_MASTER_ENABLE"], "purple", 8),
    ]
    for label, event_names, color, ax_idx in global_actuators:
        draw_signal(axes[ax_idx], event_names, color, label)

    # 3. Pulsed axle tracks - his block, once per axle instead of twice
    for k in range(n_axles):
        ax = axes[9 + k]
        label = "Axle %d Sliced Data\n(Combined)" % (k + 1)
        if k >= n_drawable:
            # The split resolved this axle but the trigger log has no complete gate
            # set at this index. An empty row with no explanation reads as "this axle
            # produced nothing"; the reason is written on the row instead.
            style_lane(ax, label, 'black')
            ax.text(0.5, 0.55,
                    "no complete gate1/gate2/gate3 set at index %d in the trigger "
                    "log - this axle has no window to slice on" % (k + 1),
                    ha='center', va='center', transform=ax.transAxes,
                    fontsize=8, color='crimson', fontweight='bold')
            continue

        ocr_r, ocr_f = g1_rises[k], g1_falls[k]
        tread_start, tread_end = g2_rises[k], g2_rises[k] + CAM_DURATION
        laser_start, laser_end = g3_rises[k], g3_falls[k]

        t_ocr, v_ocr = make_step([(ocr_r - 0.05, ocr_r + 0.05),
                                  (ocr_f - 0.05, ocr_f + 0.05)], max_sec)
        t_trd, v_trd = make_step([(tread_start, tread_end)], max_sec)
        t_lsr, v_lsr = make_pulsed_laser_step_fixed(laser_pulses_df, laser_start,
                                                    laser_end, max_sec, pad_end=0.3)

        ax.step(t_ocr, v_ocr, where='post', color='blue', linewidth=2,
                label='OCR (Gate 1 Right & Left)')
        ax.step(t_trd, v_trd, where='post', color='green', linewidth=2,
                label='Tread Mapping (Gate 2 + 2.5s)')
        ax.step(t_lsr, v_lsr, where='post', color='purple', linewidth=2,
                label='Laser Groove Pulses (Gate 3 Rise & Fall)')
        style_lane(ax, label, 'black')
        # THE LEGEND IS DRAWN ONCE, on the FIRST axle row only.
        #
        # His script puts it on both of his axle rows at loc='upper right', and with
        # two axles that is harmless - the second axle's data is in the middle of the
        # scan and the top-right corner is empty. It stops being harmless the moment
        # there is a LAST axle: that axle's own pulses are at the right-hand end of
        # the time axis, which is exactly where the box lands, and the row that most
        # needed reading was the one covered up. Verified on
        # 2026-09-07T14-09-47: axle 3's OCR, tread and laser traces all sit under it.
        #
        # The three entries are identical on every axle row, so one legend says
        # everything three of them said. It goes on axle 1, whose data is always at
        # the START of the scan, and it is drawn semi-transparent so it cannot hide a
        # trace even there.
        if k == 0:
            ax.legend(loc='upper right', frameon=True, fontsize=8.5, framealpha=0.75)

    axes[-1].set_xlabel('Time (Seconds from Vehicle Entry)', fontsize=12,
                        fontweight='bold')
    title = 'Complete System Diagram: Updated Input 10 Strictly Vehicle Enter'
    if n_axles != 2:
        title += '   —   %d axles' % n_axles
    plt.suptitle(title, fontsize=14, fontweight='bold', y=0.985)
    plt.tight_layout()
    plt.subplots_adjust(top=0.95)

    # Kept for parity with his script. Under the viewer's pyplot shim savefig is a
    # deliberate no-op, so nothing is written into the scan folder; Save Plot PNG is
    # the only thing that writes this figure anywhere.
    chart_path = os.path.join(self.output_dir, 'n_axle_vehicle_enter_diagram.png')
    plt.savefig(chart_path, dpi=300)
    plt.close()


# --- EXECUTION ---
# Deliberately empty. The marker is here because load_engineer_plot() splits every
# plot script on it before exec'ing the part above; a file with no marker is executed
# whole, and this one must never run anything on import.
