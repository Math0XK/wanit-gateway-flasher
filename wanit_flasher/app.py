"""Fenêtre principale du Wanit Gateway Flasher."""

from __future__ import annotations

import queue
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from . import APP_NAME, FIRMWARE_REPO, TARGET_CHIP, __version__, flasher, github, ports, updater
from . import theme as t
from .paths import resource

FULL_ASSET = "esp32-ble-wifi-uart-bridge-full.bin"
DEBUG_ASSET = "esp32-ble-wifi-uart-bridge-full-debug.bin"
BAUD_RATES = ["921600", "460800", "115200"]
SOURCE_RELEASE = "Release GitHub"
SOURCE_LOCAL = "Fichier local"
PORT_POLL_MS = 1500
WIDTH = 500


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=t.FONT, size=size, weight=weight)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=t.BG)
        self.title(APP_NAME)
        self.minsize(WIDTH, 420)
        self._set_icon()

        self._events: queue.Queue = queue.Queue()
        self.busy = False
        self.updating = False
        self.ports: list[ports.Port] = []
        self.releases: list[github.Release] = []
        self.releases_state = "loading"  # loading, online, offline, error
        self.local_path: Path | None = None
        self.local_check: flasher.ImageCheck | None = None
        self.pending_update: updater.Update | None = None
        self.log_open = False

        self._build_header()
        self._build_update_banner()
        self.body = ctk.CTkFrame(self, fg_color=t.CARD, corner_radius=0)
        self.body.pack(fill="both", expand=True)
        self._build_device_section()
        self._build_firmware_section()
        self._build_advanced_section()
        self._build_action_section()
        self._build_log_section()
        self._build_success_view()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_events)
        self._poll_ports()
        self._run(self._load_releases)
        self._run(self._check_update)
        self._refresh_action()
        self.after(10, self._fit)

    def _fit(self) -> None:
        """Ajuste la hauteur de la fenêtre à son contenu (panneaux repliés ou dépliés)."""
        if self.success.winfo_ismapped():
            return
        self.update_idletasks()
        height = self.winfo_reqheight() / self._get_window_scaling()
        self.geometry(f"{WIDTH}x{round(height)}")

    # --- Threads -------------------------------------------------------------------

    def _run(self, target, *args) -> None:
        threading.Thread(target=target, args=args, daemon=True).start()

    def post(self, fn, *args) -> None:
        """Exécute `fn` dans le thread de l'interface (appelable depuis n'importe quel thread)."""
        self._events.put((fn, args))

    def _drain_events(self) -> None:
        try:
            while True:
                fn, args = self._events.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(50, self._drain_events)

    # --- Construction de l'interface -----------------------------------------------

    def _set_icon(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Wanit.GatewayFlasher")
        except Exception:
            pass
        icon = str(resource("icon.ico"))
        # CustomTkinter impose son icône par défaut après ~200 ms : on repasse derrière
        self.after(300, lambda: self.iconbitmap(icon))

    def _build_header(self) -> None:
        header = self.header = ctk.CTkFrame(self, fg_color=t.NAVY, corner_radius=0, height=68)
        header.pack(fill="x")
        header.pack_propagate(False)

        mark = Image.open(resource("mark.png"))
        ctk.CTkLabel(header, text="", image=ctk.CTkImage(mark, size=(round(36 * mark.width / mark.height), 36))).pack(
            side="left", padx=(18, 12)
        )
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", pady=12)
        word = Image.open(resource("wordmark_white.png"))
        ctk.CTkLabel(titles, text="", height=16, image=ctk.CTkImage(word, size=(round(14 * word.width / word.height), 14))).pack(
            anchor="w"
        )
        self.version_label = ctk.CTkLabel(
            titles, text=f"Gateway Flasher · v{__version__}", height=18, font=font(11), text_color=t.NAVY_MUTED
        )
        self.version_label.pack(anchor="w")

        self.gear = ctk.CTkButton(
            header, text="⚙", width=36, height=36, font=ctk.CTkFont(family="Segoe UI Symbol", size=18),
            fg_color="transparent", hover_color="#33496A", text_color=t.NAVY_MUTED, command=self._toggle_advanced,
        )
        self.gear.pack(side="right", padx=12)

    def _build_update_banner(self) -> None:
        self.banner = ctk.CTkFrame(self, fg_color=t.BLUE_TINT, corner_radius=0, height=38)
        self.banner_label = ctk.CTkLabel(self.banner, text="", font=font(12), text_color=t.BLUE_TEXT)
        self.banner_label.pack(side="left", padx=18)
        self.banner_button = ctk.CTkButton(
            self.banner, text="Mettre à jour", width=110, height=26, font=font(12, "bold"), corner_radius=t.RADIUS,
            fg_color=t.BLUE, hover_color=t.BLUE_HOVER, command=self._install_update,
        )
        self.banner_button.pack(side="right", padx=12, pady=6)

    def _section_title(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=font(11, "bold"), text_color=t.TEXT_MUTED, height=20).pack(anchor="w")

    def _option_menu(self, parent, command, **kwargs) -> ctk.CTkOptionMenu:
        return ctk.CTkOptionMenu(
            parent, height=36, corner_radius=t.RADIUS, font=font(13), dropdown_font=font(13),
            fg_color=t.FIELD, button_color=t.FIELD, button_hover_color=t.DIVIDER, text_color=t.TEXT,
            text_color_disabled=t.TEXT_HINT, dropdown_fg_color=t.CARD, dropdown_hover_color=t.BLUE_TINT,
            dropdown_text_color=t.TEXT, dynamic_resizing=False, command=command, **kwargs,
        )

    def _secondary_button(self, parent, text: str, command, **kwargs) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=36, corner_radius=t.RADIUS, font=font(13), fg_color=t.FIELD,
            hover_color=t.DIVIDER, text_color=t.TEXT, border_width=1, border_color=t.BORDER, command=command, **kwargs,
        )

    def _build_device_section(self) -> None:
        section = ctk.CTkFrame(self.body, fg_color="transparent")
        section.pack(fill="x", padx=20, pady=(18, 0))
        self._section_title(section, "1 · APPAREIL")
        row = ctk.CTkFrame(section, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self.port_menu = self._option_menu(row, lambda _: self._on_port_selected(), values=["Aucun appareil détecté"])
        self.port_menu.pack(side="left", fill="x", expand=True)
        self._secondary_button(row, "↻", self._poll_ports_now, width=36).pack(side="left", padx=(6, 0))
        self.port_status = ctk.CTkLabel(section, text="", font=font(12), height=20, anchor="w")
        self.port_status.pack(fill="x", pady=(4, 0))

    def _build_firmware_section(self) -> None:
        section = ctk.CTkFrame(self.body, fg_color="transparent")
        section.pack(fill="x", padx=20, pady=(14, 0))
        self._section_title(section, "2 · FIRMWARE")
        self.source = ctk.CTkSegmentedButton(
            section, values=[SOURCE_RELEASE, SOURCE_LOCAL], height=32, font=font(12), corner_radius=t.RADIUS,
            fg_color=t.DIVIDER, selected_color=t.CARD, selected_hover_color=t.CARD, unselected_color=t.DIVIDER,
            unselected_hover_color="#E2E8EF", text_color=t.TEXT, command=lambda _: self._on_source_changed(),
        )
        self.source.set(SOURCE_RELEASE)
        self.source.pack(fill="x", pady=(4, 8))

        self.source_container = ctk.CTkFrame(section, fg_color="transparent")
        self.source_container.pack(fill="x")
        self.release_frame = ctk.CTkFrame(self.source_container, fg_color="transparent")
        self.release_menu = self._option_menu(self.release_frame, lambda _: self._on_release_selected(), values=["Chargement des versions…"])
        self.release_menu.pack(fill="x")
        info = ctk.CTkFrame(self.release_frame, fg_color="transparent")
        info.pack(fill="x", pady=(4, 0))
        self.release_info = ctk.CTkLabel(info, text="", font=font(12), text_color=t.TEXT_HINT, height=20, anchor="w")
        self.release_info.pack(side="left")
        self.notes_link = ctk.CTkLabel(info, text="Notes de version ↗", font=font(12), text_color=t.BLUE, height=20, cursor="hand2")
        self.notes_link.bind("<Button-1>", lambda _: self._open_release_notes())

        self.local_frame = ctk.CTkFrame(self.source_container, fg_color="transparent")
        row = ctk.CTkFrame(self.local_frame, fg_color="transparent")
        row.pack(fill="x")
        self.local_entry = ctk.CTkEntry(
            row, height=36, corner_radius=t.RADIUS, font=font(13), fg_color=t.FIELD, border_color=t.BORDER, border_width=1,
            text_color=t.TEXT, placeholder_text="esp32-ble-wifi-uart-bridge-full.bin", placeholder_text_color=t.TEXT_HINT,
        )
        self.local_entry.pack(side="left", fill="x", expand=True)
        self.local_entry.configure(state="disabled")
        self._secondary_button(row, "Parcourir…", self._browse_local, width=100).pack(side="left", padx=(6, 0))
        self.local_info = ctk.CTkLabel(self.local_frame, text="", font=font(12), height=20, anchor="w", justify="left", wraplength=WIDTH - 50)
        self.local_info.pack(fill="x", pady=(4, 0))

        self.firmware_warning = ctk.CTkLabel(
            section, text="", font=font(12), text_color=t.WARNING, anchor="w", justify="left", wraplength=WIDTH - 50
        )
        self.release_frame.pack(fill="x")

    def _build_advanced_section(self) -> None:
        self.advanced = ctk.CTkFrame(self.body, fg_color=t.FIELD, corner_radius=8, border_width=1, border_color=t.BORDER)
        inner = ctk.CTkFrame(self.advanced, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(inner, text="Options avancées", font=font(13, "bold"), text_color=t.TEXT, height=22).pack(anchor="w", pady=(0, 4))

        switch_style = dict(
            font=font(13), text_color=t.TEXT, progress_color=t.BLUE, button_color=t.CARD,
            button_hover_color=t.CARD, fg_color=t.DISABLED, switch_width=38, switch_height=20,
        )
        self.debug_switch = ctk.CTkSwitch(inner, text="Variante debug", command=self._on_options_changed, **switch_style)
        self.debug_switch.pack(anchor="w", pady=3)
        self.erase_switch = ctk.CTkSwitch(inner, text="Effacer toute la flash avant d'écrire", command=self._on_options_changed, **switch_style)
        self.erase_switch.pack(anchor="w", pady=(3, 0))
        ctk.CTkLabel(
            inner, text="Supprime le Wi-Fi provisionné, l'UID, le nom et le token d'authentification.",
            font=font(11), text_color=t.WARNING, height=18, anchor="w",
        ).pack(fill="x", padx=(46, 0))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(row, text="Vitesse de transfert", font=font(13), text_color=t.TEXT).pack(side="left")
        self.baud_menu = self._option_menu(row, None, values=BAUD_RATES, width=110)
        self.baud_menu.configure(height=30)
        self.baud_menu.pack(side="right")

    def _build_action_section(self) -> None:
        self.action = ctk.CTkFrame(self.body, fg_color="transparent")
        self.action.pack(fill="x", padx=20, pady=(18, 0))

        self.error_box = ctk.CTkFrame(self.action, fg_color=t.DANGER_TINT, corner_radius=t.RADIUS)
        self.error_label = ctk.CTkLabel(
            self.error_box, text="", font=font(12), text_color=t.DANGER, justify="left", anchor="w", wraplength=WIDTH - 70
        )
        self.error_label.pack(fill="x", padx=12, pady=8)

        self.flash_button = ctk.CTkButton(
            self.action, text="", height=46, corner_radius=t.RADIUS, font=font(15, "bold"), fg_color=t.BLUE,
            hover_color=t.BLUE_HOVER, text_color="#FFFFFF", text_color_disabled="#FFFFFF", command=self._start_flash,
        )
        self.flash_button.pack(fill="x")

        self.progress_frame = ctk.CTkFrame(self.action, fg_color="transparent")
        labels = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        labels.pack(fill="x")
        self.progress_label = ctk.CTkLabel(labels, text="", font=font(12), text_color=t.TEXT_MUTED, height=20)
        self.progress_label.pack(side="left")
        self.progress_pct = ctk.CTkLabel(labels, text="", font=font(12), text_color=t.TEXT_MUTED, height=20)
        self.progress_pct.pack(side="right")
        self.progress = ctk.CTkProgressBar(self.progress_frame, height=6, corner_radius=3, fg_color=t.DIVIDER, progress_color=t.BLUE)
        self.progress.pack(fill="x", pady=(2, 0))

    def _build_log_section(self) -> None:
        self.log_section = ctk.CTkFrame(self.body, fg_color="transparent")
        self.log_section.pack(fill="both", expand=True, padx=20, pady=(14, 14))
        ctk.CTkFrame(self.log_section, fg_color=t.DIVIDER, height=1).pack(fill="x")
        bar = ctk.CTkFrame(self.log_section, fg_color="transparent")
        bar.pack(fill="x", pady=(4, 0))
        link = dict(fg_color="transparent", hover_color=t.DIVIDER, text_color=t.TEXT_HINT, font=font(12), height=26, corner_radius=t.RADIUS)
        self.log_toggle = ctk.CTkButton(bar, text="▸  Journal", width=90, anchor="w", command=self._toggle_log, **link)
        self.log_toggle.pack(side="left")
        ctk.CTkButton(bar, text="Copier", width=60, command=self._copy_log, **link).pack(side="right")
        self.log_box = ctk.CTkTextbox(
            self.log_section, font=ctk.CTkFont(family=t.MONO, size=11), fg_color=t.FIELD, text_color=t.TEXT,
            border_color=t.BORDER, border_width=1, corner_radius=t.RADIUS, wrap="word", height=140,
        )
        self.log_box.configure(state="disabled")

    def _build_success_view(self) -> None:
        self.success = ctk.CTkFrame(self, fg_color=t.CARD, corner_radius=0)
        inner = ctk.CTkFrame(self.success, fg_color="transparent")
        inner.place(relx=0.5, rely=0.42, anchor="center")
        badge = ctk.CTkFrame(inner, width=64, height=64, corner_radius=32, fg_color=t.SUCCESS_TINT)
        badge.pack(pady=(0, 14))
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge, text="✓", text_color=t.SUCCESS, font=ctk.CTkFont(family="Segoe UI Symbol", size=28, weight="bold")
        ).place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(inner, text="Flash terminé", font=font(18, "bold"), text_color=t.TEXT).pack()
        self.success_label = ctk.CTkLabel(inner, text="", font=font(13), text_color=t.TEXT_MUTED, wraplength=WIDTH - 100, justify="center")
        self.success_label.pack(pady=(6, 20))
        ctk.CTkButton(
            inner, text="Flasher un autre appareil", height=40, width=240, corner_radius=t.RADIUS, font=font(13, "bold"),
            fg_color="transparent", hover_color=t.BLUE_TINT, border_width=1, border_color=t.BLUE, text_color=t.BLUE,
            command=self._back_to_form,
        ).pack()

    # --- Ports ---------------------------------------------------------------------

    def _poll_ports(self) -> None:
        if not self.busy:
            self._update_ports(ports.scan())
        self.after(PORT_POLL_MS, self._poll_ports)

    def _poll_ports_now(self) -> None:
        if not self.busy:
            self._update_ports(ports.scan())

    def _update_ports(self, found: list[ports.Port]) -> None:
        if found == self.ports:
            return
        previous = {p.device for p in self.ports}
        current = self.selected_port()
        self.ports = found
        if not found:
            self.port_menu.configure(values=["Aucun appareil détecté"])
            self.port_menu.set("Aucun appareil détecté")
        else:
            self.port_menu.configure(values=[p.label for p in found])
            # Un appareil ESP32 qu'on vient de brancher devient la sélection
            plugged = [p for p in found if p.device not in previous and p.kind != "other"]
            keep = next((p for p in found if current and p.device == current.device), None)
            choice = plugged[0] if plugged else keep or found[0]
            self.port_menu.set(choice.label)
        self._on_port_selected()

    def selected_port(self) -> ports.Port | None:
        label = self.port_menu.get()
        return next((p for p in self.ports if p.label == label), None)

    def _on_port_selected(self) -> None:
        port = self.selected_port()
        if port is None:
            self.port_status.configure(text="Branchez le module de flash sur la carte et sur un port USB.", text_color=t.TEXT_HINT)
        elif port.kind == "espressif":
            self.port_status.configure(text="● Appareil Espressif détecté", text_color=t.SUCCESS)
        elif port.kind == "bridge":
            text = "● Module de flash détecté" if port.description.startswith("Module") else "● Adaptateur USB-série détecté"
            self.port_status.configure(text=text, text_color=t.SUCCESS)
        else:
            self.port_status.configure(text="Port série non identifié : vérifiez qu'il s'agit bien de la carte.", text_color=t.TEXT_HINT)
        self._refresh_action()

    # --- Firmware ------------------------------------------------------------------

    def _load_releases(self) -> None:
        try:
            releases, offline = github.firmware_releases(FIRMWARE_REPO)
        except github.GitHubError:
            self.post(self._on_releases_loaded, [], "error")
            return
        usable = [r for r in releases if FULL_ASSET in r.assets]
        if offline:
            usable = [r for r in usable if github.is_cached(r, r.assets[FULL_ASSET])]
        self.post(self._on_releases_loaded, usable, "offline" if offline else "online")

    def _on_releases_loaded(self, releases: list[github.Release], state: str) -> None:
        self.releases = releases
        self.releases_state = state
        if not releases:
            text = "Aucune version disponible hors ligne" if state != "online" else "Aucune version publiée"
            self.release_menu.configure(values=[text], state="disabled")
            self.release_menu.set(text)
            if state != "online":
                message = "GitHub est injoignable et aucune version n'est en cache. Choisissez un fichier local."
                self.release_info.configure(text=message, text_color=t.WARNING)
                if self.local_path is None:
                    self.local_info.configure(text=message, text_color=t.WARNING)
                self.source.set(SOURCE_LOCAL)
                self._on_source_changed()
        else:
            latest = next((r for r in releases if not r.prerelease), releases[0])
            labels = [self._release_label(r, r is latest) for r in releases]
            self.release_menu.configure(values=labels, state="normal")
            self.release_menu.set(labels[releases.index(latest)])
        self._on_release_selected()

    @staticmethod
    def _release_label(release: github.Release, latest: bool) -> str:
        day = "/".join(reversed(release.published.split("-"))) if release.published else ""
        tags = " — dernière" if latest else " — pré-version" if release.prerelease else ""
        return f"{release.tag}{tags}   ·   {day}"

    def selected_release(self) -> github.Release | None:
        label = self.release_menu.get()
        return next((r for r in self.releases if label.startswith(r.tag + " ") or label == r.tag), None)

    def selected_asset(self) -> github.Asset | None:
        release = self.selected_release()
        if release is None:
            return None
        name = DEBUG_ASSET if self.debug_switch.get() else FULL_ASSET
        return release.assets.get(name)

    def _on_release_selected(self) -> None:
        release, asset = self.selected_release(), self.selected_asset()
        self.notes_link.pack_forget()
        if release and asset:
            size = f"{asset.size / 1_000_000:.1f} Mo".replace(".", ",")
            extra = " · en cache" if github.is_cached(release, asset) else ""
            offline = " · hors ligne" if self.releases_state == "offline" else ""
            self.release_info.configure(text=f"{'Debug' if self.debug_switch.get() else 'Standard'} · {size}{extra}{offline}", text_color=t.TEXT_HINT)
            self.notes_link.pack(side="left", padx=(8, 0))
        elif release and self.debug_switch.get():
            self.release_info.configure(text="Pas de variante debug pour cette version.", text_color=t.WARNING)
        elif self.releases_state == "loading":
            self.release_info.configure(text="Récupération des versions sur GitHub…", text_color=t.TEXT_HINT)
        self._refresh_warning()
        self._refresh_action()

    def _open_release_notes(self) -> None:
        release = self.selected_release()
        if release:
            webbrowser.open(release.page_url)

    def _on_source_changed(self) -> None:
        self.after_idle(self._fit)
        shown, hidden = (self.release_frame, self.local_frame) if self.source.get() == SOURCE_RELEASE else (self.local_frame, self.release_frame)
        hidden.pack_forget()
        shown.pack(fill="x")
        self._refresh_warning()
        self._refresh_action()

    def _browse_local(self) -> None:
        path = filedialog.askopenfilename(
            title="Choisir l'image firmware", filetypes=[("Image firmware", "*.bin"), ("Tous les fichiers", "*.*")]
        )
        if not path:
            return
        self.local_path = Path(path)
        self.local_check = flasher.check_image(self.local_path)
        self.local_entry.configure(state="normal")
        self.local_entry.delete(0, "end")
        self.local_entry.insert(0, self.local_path.name)
        self.local_entry.configure(state="disabled")
        color = t.SUCCESS if self.local_check.ok else t.DANGER
        self.local_info.configure(text=("✓ " if self.local_check.ok else "✕ ") + self.local_check.message, text_color=color)
        self._refresh_warning()
        self._refresh_action()

    def _on_options_changed(self) -> None:
        self._on_release_selected()

    def _refresh_warning(self) -> None:
        self.after_idle(self._fit)
        if self.source.get() == SOURCE_RELEASE:
            warning = "Variante debug : expose des informations sensibles sur l'UART, à ne pas déployer en production." if self.debug_switch.get() else None
        else:
            warning = self.local_check.warning if self.local_check and self.local_check.ok else None
        if warning:
            self.firmware_warning.configure(text="⚠ " + warning)
            self.firmware_warning.pack(fill="x", pady=(4, 0))
        else:
            self.firmware_warning.pack_forget()

    # --- Options avancées et journal -----------------------------------------------

    def _toggle_advanced(self) -> None:
        self.after_idle(self._fit)
        if self.advanced.winfo_ismapped():
            self.advanced.pack_forget()
            self.gear.configure(text_color=t.NAVY_MUTED)
        else:
            self.advanced.pack(fill="x", padx=20, pady=(14, 0), before=self.action)
            self.gear.configure(text_color="#FFFFFF")

    def _toggle_log(self, force: bool | None = None) -> None:
        self.after_idle(self._fit)
        self.log_open = (not self.log_open) if force is None else force
        if self.log_open:
            self.log_box.pack(fill="both", expand=True, pady=(4, 0))
            self.log_toggle.configure(text="▾  Journal")
        else:
            self.log_box.pack_forget()
            self.log_toggle.configure(text="▸  Journal")

    def append_log(self, line: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.log_box.get("1.0", "end").strip())

    # --- Action principale -----------------------------------------------------------

    def _target_name(self) -> str | None:
        """Nom court de ce qui va être flashé, ou None si la sélection est incomplète."""
        if self.source.get() == SOURCE_RELEASE:
            release, asset = self.selected_release(), self.selected_asset()
            if not (release and asset):
                return None
            return release.tag + (" debug" if asset.name == DEBUG_ASSET else "")
        if self.local_path and self.local_check and self.local_check.ok:
            name = self.local_path.name
            return name if len(name) <= 24 else name[:21] + "…"
        return None

    def _refresh_action(self) -> None:
        if self.busy or self.updating:
            text = "Flash en cours…" if self.busy else "Mise à jour de l'utilitaire…"
            self.flash_button.configure(text=text, state="disabled", fg_color=t.BLUE)
            return
        port, target = self.selected_port(), self._target_name()
        if port is None:
            text = "Branchez un appareil"
        elif target is None:
            if self.source.get() == SOURCE_LOCAL:
                text = "Choisissez un fichier .bin" if not self.local_path else "Fichier non valide"
            else:
                text = "Chargement des versions…" if self.releases_state == "loading" else "Aucune version sélectionnée"
        else:
            self.flash_button.configure(text=f"Flasher {target} sur {port.device}", state="normal", fg_color=t.BLUE)
            return
        self.flash_button.configure(text=text, state="disabled", fg_color=t.DISABLED)

    def _show_error(self, message: str | None) -> None:
        self.after_idle(self._fit)
        if message:
            self.error_label.configure(text=message)
            self.error_box.pack(fill="x", pady=(0, 10), before=self.flash_button)
        else:
            self.error_box.pack_forget()

    def _set_busy(self, busy: bool) -> None:
        self.after_idle(self._fit)
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.port_menu, self.source, self.release_menu, self.debug_switch, self.erase_switch, self.baud_menu):
            widget.configure(state=state)
        if busy:
            self.progress_frame.pack(fill="x", pady=(12, 0))
        else:
            self.progress.stop()
            self.progress_frame.pack_forget()
            if not self.releases:
                self.release_menu.configure(state="disabled")
        self._refresh_action()

    def _start_flash(self) -> None:
        port, target = self.selected_port(), self._target_name()
        if self.busy or self.updating or port is None or target is None:
            return
        if self.erase_switch.get() and not messagebox.askyesno(
            APP_NAME,
            "L'effacement complet supprime le Wi-Fi provisionné, l'UID, le nom et le token d'authentification.\n\nContinuer ?",
            icon="warning",
        ):
            return

        self._show_error(None)
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self._set_busy(True)
        job = dict(
            port=port.device,
            target=target,
            release=self.selected_release() if self.source.get() == SOURCE_RELEASE else None,
            asset=self.selected_asset() if self.source.get() == SOURCE_RELEASE else None,
            local=self.local_path if self.source.get() == SOURCE_LOCAL else None,
            baud=int(self.baud_menu.get()),
            erase=bool(self.erase_switch.get()),
        )
        self._run(self._flash_worker, job)

    def _flash_worker(self, job: dict) -> None:
        log = lambda line: self.post(self.append_log, line)  # noqa: E731
        try:
            if job["release"]:
                release, asset = job["release"], job["asset"]
                log(f"Firmware {release.tag} : {asset.name}")
                image = github.download(
                    asset, github.firmware_path(release, asset), lambda done, total: self.post(self._on_progress, "download", done, total)
                )
                log("Empreinte SHA-256 vérifiée" if asset.sha256 else "Pas d'empreinte publiée pour ce fichier")
            else:
                image = job["local"]
                log(f"Fichier local : {image}")
            check = flasher.check_image(image)
            if not check.ok:
                raise flasher.FlashError(check.message)
            log(f"Port {job['port']} · {job['baud']} bauds" + (" · effacement complet" if job["erase"] else ""))
            mac = flasher.flash(
                job["port"], image, job["baud"], job["erase"], log, lambda stage, done, total: self.post(self._on_progress, stage, done, total)
            )
        except (flasher.FlashError, github.GitHubError) as exc:
            self.post(self._on_flash_failed, str(exc))
            return
        except Exception as exc:  # garde-fou : l'interface ne doit jamais rester bloquée
            log(f"Erreur : {type(exc).__name__}: {exc}")
            self.post(self._on_flash_failed, f"Erreur inattendue : {exc}")
            return
        self.post(self._on_flash_done, job, mac)

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        labels = {
            "download": "Téléchargement du firmware…",
            "connect": f"Connexion à l'{TARGET_CHIP}…",
            "erase": "Effacement de la flash…",
            "write": "Écriture…",
            "reset": "Redémarrage de l'appareil…",
        }
        self.progress_label.configure(text=labels.get(stage, ""))
        if stage in ("connect", "erase"):
            if self.progress.cget("mode") != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start()
            self.progress_pct.configure(text="")
            return
        if self.progress.cget("mode") != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        ratio = done / total if total else 1
        self.progress.set(ratio)
        self.progress_pct.configure(text=f"{round(ratio * 100)} %")

    def _on_flash_failed(self, message: str) -> None:
        self._set_busy(False)
        self._show_error(message)
        self._toggle_log(True)
        self._on_release_selected()

    def _on_flash_done(self, job: dict, mac: str) -> None:
        self._set_busy(False)
        self.success_label.configure(
            text=f"{job['target']} est installé sur {job['port']} (MAC {mac}).\n\n"
            "L'appareil redémarre. S'il n'est pas encore provisionné, il apparaît en BLE sous le nom « Wanit SC »."
        )
        self.body.pack_forget()
        self.success.pack(fill="both", expand=True)
        self._on_release_selected()  # « en cache » est maintenant vrai

    def _back_to_form(self) -> None:
        self.after_idle(self._fit)
        self.success.pack_forget()
        self.body.pack(fill="both", expand=True)
        self.progress.set(0)

    # --- Auto-mise à jour ------------------------------------------------------------

    def _check_update(self) -> None:
        try:
            updater.cleanup_previous()
        except OSError:
            pass
        try:
            update = updater.check()
        except github.GitHubError:
            self.post(lambda: self.version_label.configure(text=f"Gateway Flasher · v{__version__} · hors ligne"))
            return
        if update:
            self.post(self._show_update, update)

    def _show_update(self, update: updater.Update) -> None:
        self.after_idle(self._fit)
        self.pending_update = update
        self.banner_label.configure(text=f"La version {update.version} est disponible.")
        self.banner_button.configure(text="Mettre à jour" if updater.can_self_update(update) else "Voir sur GitHub")
        self.banner.pack(fill="x", after=self.header)

    def _install_update(self) -> None:
        update = self.pending_update
        if update is None:
            return
        if not updater.can_self_update(update):
            webbrowser.open(update.page_url)
            return
        if self.busy:
            self.banner_label.configure(text="Attendez la fin du flash pour mettre à jour.")
            return
        self.updating = True
        self.banner_button.configure(state="disabled")
        self._refresh_action()

        def progress(done: int, total: int) -> None:
            text = f"Téléchargement de la version {update.version}… {round(done * 100 / total)} %"
            self.post(lambda: self.banner_label.configure(text=text))

        def worker() -> None:
            try:
                updater.install(update, progress)
            except github.GitHubError as exc:
                self.post(self._on_update_failed, str(exc))
                return
            self.post(self.destroy)

        self._run(worker)

    def _on_update_failed(self, message: str) -> None:
        self.updating = False
        self.banner_label.configure(text=message, wraplength=WIDTH - 170, justify="left")
        self.banner_button.configure(text="Voir sur GitHub", state="normal", command=lambda: webbrowser.open(self.pending_update.page_url))
        self._refresh_action()

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
            APP_NAME, "Un flash est en cours. Quitter maintenant peut laisser l'appareil sans firmware valide.\n\nQuitter quand même ?",
            icon="warning",
        ):
            return
        self.destroy()


def main() -> None:
    ctk.set_appearance_mode("light")
    App().mainloop()
