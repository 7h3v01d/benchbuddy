"""Parts lookup / inventory tab: search, inventory, CSV, photos and photo identification."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                             QPushButton, QSizePolicy, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..core import ocr
from ..core.identify import PhotoMatch, identify, match_candidates
from ..core.partsdb import Part, PartsDB
from .widgets import ResultView

COLS = ["Part", "Category", "Description", "In stock", "Location"]
IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.bmp *.gif *.webp)"


class PartDialog(QDialog):
    def __init__(self, categories: list[str], part: Part | None = None, parent=None, prefill: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit part" if part else "Add part")
        self.setMinimumWidth(480)
        pre = prefill or {}
        g = (lambda attr, key: getattr(part, attr) if part else pre.get(key, ""))
        self.pn = QLineEdit(g("part_number", "part_number"))
        self.cat = QComboBox()
        self.cat.setEditable(True)
        self.cat.addItems(categories)
        self.cat.setCurrentText(part.category if part else pre.get("category", "Other"))
        self.desc = QLineEdit(g("description", "description"))
        self.pkg = QLineEdit(g("package", "package"))
        self.specs = QLineEdit(g("specs", "specs"))
        self.marks = QLineEdit(g("markings", "markings"))
        self.marks.setPlaceholderText("comma separated, e.g. J3Y, 2TY")
        self.qty = QSpinBox()
        self.qty.setRange(0, 1_000_000)
        self.qty.setValue(part.qty if part else int(pre.get("qty", 0)))
        self.loc = QLineEdit(g("location", "location"))
        self.loc.setPlaceholderText("e.g. Drawer B2, Blue box")
        self.ds = QLineEdit(g("datasheet_url", "datasheet_url"))
        self.ds.setPlaceholderText("optional: link to the datasheet (a web search is offered if empty)")
        self.notes = QPlainTextEdit(g("notes", "notes"))
        self.notes.setFixedHeight(70)
        f = QFormLayout()
        for label, w in (("Part number:", self.pn), ("Category:", self.cat), ("Description:", self.desc),
                         ("Package:", self.pkg), ("Key specs:", self.specs), ("Top markings:", self.marks),
                         ("Quantity owned:", self.qty), ("Location:", self.loc), ("Datasheet link:", self.ds),
                         ("Notes:", self.notes)):
            f.addRow(label, w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(buttons)

    def _accept(self) -> None:
        if not self.pn.text().strip():
            QMessageBox.warning(self, "Part number needed", "Please enter a part number.")
            return
        self.accept()

    def values(self) -> dict:
        return dict(part_number=self.pn.text().strip(), category=self.cat.currentText().strip() or "Other",
                    description=self.desc.text().strip(), package=self.pkg.text().strip(),
                    specs=self.specs.text().strip(), markings=self.marks.text().strip(),
                    notes=self.notes.toPlainText().strip(), qty=self.qty.value(), location=self.loc.text().strip(),
                    datasheet_url=self.ds.text().strip())


class OcrWorker(QThread):
    """Runs OCR off the GUI thread so the window stays responsive."""
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path

    def run(self) -> None:
        try:
            self.done.emit(ocr.read_markings(self.image_path))
        except Exception as exc:  # noqa: BLE001 - report anything (missing tesseract, bad image) to the user
            self.failed.emit(str(exc))


class PhotoIdentifyDialog(QDialog):
    """Show a photo, read its markings with OCR and match them against the library."""

    def __init__(self, db: PartsDB, image_path: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.image_path = image_path
        self.matches: list[PhotoMatch] = []
        self.tokens: list[str] = []
        self.chosen_part_id: int | None = None
        self.chosen_token: str | None = None
        self.add_new = False
        self.setWindowTitle("Identify from photo")
        self.setMinimumSize(820, 540)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(image_path)
        if not pix.isNull():
            self.preview.setPixmap(pix.scaled(340, 340, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText("(couldn't show preview)")
        self.status = QLabel("Reading the markings…")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(64)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.token_list = QListWidget()
        self.token_list.setToolTip("Text the OCR found. Pick the one that matches what is printed on your part.")
        self.match_list = QListWidget()
        self.match_list.setWordWrap(True)
        self.match_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.match_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.match_list.itemDoubleClicked.connect(lambda _i: self._use_match())
        self.use_btn = QPushButton("Show selected part in library")
        self.use_btn.clicked.connect(self._use_match)
        self.search_btn = QPushButton("Search library for selected text")
        self.search_btn.clicked.connect(self._use_token)
        self.new_btn = QPushButton("Add as new part…")
        self.new_btn.clicked.connect(self._add_new)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (self.use_btn, self.search_btn, self.new_btn):
            b.setEnabled(False)

        left = QVBoxLayout()
        left.addWidget(self.preview, 1)
        right = QVBoxLayout()
        right.addWidget(self.status)
        right.addWidget(QLabel("Library matches (best first):"))
        right.addWidget(self.match_list, 2)
        right.addWidget(self.use_btn)
        right.addWidget(QLabel("Text found in the photo:"))
        right.addWidget(self.token_list, 1)
        row = QHBoxLayout()
        row.addWidget(self.search_btn)
        row.addWidget(self.new_btn)
        right.addLayout(row)
        right.addWidget(close)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addLayout(right, 1)

        ok, why = ocr.ocr_available()
        if not ok:
            self.worker = None
            self.status.setText(f"<b>Can't read photos yet:</b> {why}<br><pre>{ocr.INSTALL_HINT}</pre>")
            self.status.setTextFormat(Qt.TextFormat.RichText)
            return
        self.worker = OcrWorker(image_path, self)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    # ----------------------------------------------------------------- results
    def _on_failed(self, msg: str) -> None:
        self.status.setText(f"Couldn't read the photo: {msg}")

    def _on_done(self, tokens: list) -> None:
        self.tokens = tokens
        if not tokens:
            self.status.setText("No text found. Try a closer, sharper, evenly lit photo of the printed side, "
                                "filling the frame. Light print on a dark body works best.")
            return
        for t in tokens:
            self.token_list.addItem(t)
        self.token_list.setCurrentRow(0)
        self.matches = match_candidates(tokens, self.db)
        for m in self.matches:
            item = QListWidgetItem(f"{m.part_number}: {m.description}\n({m.note})")
            item.setData(Qt.ItemDataRole.UserRole, m.part_id)
            self.match_list.addItem(item)
        if self.matches:
            self.match_list.setCurrentRow(0)
            self.status.setText(f"Found {len(tokens)} possible reading(s) and {len(self.matches)} library match(es). "
                                f"OCR can mistake look-alike characters, so check the result against the part.")
            self.use_btn.setEnabled(True)
        else:
            self.status.setText("Text found, but nothing in the library matches. Pick the right reading and add it "
                                "as a new part, or search for it.")
        self.search_btn.setEnabled(True)
        self.new_btn.setEnabled(True)

    def _use_match(self) -> None:
        item = self.match_list.currentItem()
        if item is not None:
            self.chosen_part_id = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def _use_token(self) -> None:
        item = self.token_list.currentItem()
        if item is not None:
            self.chosen_token = item.text()
            self.accept()

    def _add_new(self) -> None:
        item = self.token_list.currentItem()
        if item is not None:
            self.chosen_token = item.text()
            self.add_new = True
            self.accept()

    def done(self, code: int) -> None:  # noqa: A003 - Qt API name
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(35000)
        super().done(code)


class PartsTab(QWidget):
    def __init__(self, db: PartsDB, parent=None):
        super().__init__(parent)
        self.db = db
        self.results: list[Part] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type what's printed on the part (J3Y, 2N3904, 1117, 104…) or keywords "
                                       "(logic level mosfet, 3.3V regulator)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.category = QComboBox()
        self.category.currentTextChanged.connect(self.refresh)
        self.in_stock = QCheckBox("Only parts I own")
        self.in_stock.toggled.connect(self.refresh)
        photo = QPushButton("📷 Identify from photo…")
        photo.setToolTip("Read the markings from a picture of a component and look them up")
        photo.clicked.connect(self.identify_photo)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.category)
        top.addWidget(self.in_stock)
        top.addWidget(photo)

        self.decode = ResultView(60)
        self.decode.setMaximumHeight(110)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.table.itemDoubleClicked.connect(lambda _i: self.edit_part())
        self.detail = ResultView(120)

        def button(text, slot, tip=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if tip:
                b.setToolTip(tip)
            return b

        row1 = QHBoxLayout()
        for b in (button("Add part…", self.add_part), button("Edit…", self.edit_part),
                  button("Delete", self.delete_part)):
            row1.addWidget(b)
        row1.addStretch(1)
        row1.addWidget(button("−1 owned", lambda: self.bump(-1)))
        row1.addWidget(button("+1 owned", lambda: self.bump(1)))
        row2 = QHBoxLayout()
        row2.addWidget(button("Attach photo…", self.attach_photo, "Keep a picture of this part with its entry"))
        row2.addWidget(button("Remove photo", self.remove_photo))
        row2.addStretch(1)
        row2.addWidget(button("Import CSV…", self.import_csv, "Add or update parts from a spreadsheet export"))
        row2.addWidget(button("Export CSV…", self.export_csv, "Back up or edit your inventory in a spreadsheet"))

        self.count_label = QLabel()
        self.count_label.setProperty("role", "muted")

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.table, 1)
        ll.addLayout(row1)
        ll.addLayout(row2)
        ll.addWidget(self.count_label)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.decode)
        lay.addWidget(split, 1)
        self.reload_categories()
        self.refresh()

    # ----------------------------------------------------------------- state
    def reload_categories(self) -> None:
        cur = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All")
        self.category.addItems(self.db.categories())
        self.category.setCurrentText(cur if cur else "All")
        self.category.blockSignals(False)

    def selected_part(self) -> Part | None:
        row = self.table.currentRow()
        return self.results[row] if 0 <= row < len(self.results) else None

    def refresh(self, *_args, keep_id: int | None = None) -> None:
        q = self.search.text()
        self.results = self.db.search(q, self.category.currentText(), self.in_stock.isChecked())
        self.table.setRowCount(len(self.results))
        for row, p in enumerate(self.results):
            vals = [p.part_number, p.category, p.description, str(p.qty) if p.qty else "—", p.location]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if p.qty and col == 3:
                    item.setForeground(Qt.GlobalColor.darkGreen)
                self.table.setItem(row, col, item)
        self.count_label.setText(f"{len(self.results)} part(s) shown · {len(self.db.all())} in library")
        if keep_id is not None:
            for row, p in enumerate(self.results):
                if p.id == keep_id:
                    self.table.selectRow(row)
                    break
        elif self.results:
            self.table.selectRow(0)
        self._update_decode(q)
        self.show_detail()

    def _update_decode(self, q: str) -> None:
        if not q.strip():
            self.decode.hide()
            return
        guesses = [m for m in identify(q) if m.kind != "part"]
        if not guesses:
            self.decode.hide()
            return
        self.decode.show()
        html = "".join(f"<p style='margin:2px 0'>🔎 <b>{m.title}</b> "
                       f"<span class='muted'>— {m.detail}</span></p>" for m in guesses)
        self.decode.show_html(html)

    def show_detail(self) -> None:
        p = self.selected_part()
        if not p:
            self.detail.show_html("<span class='muted'>No part selected. Try searching for what's written on the "
                                  "component, or add your own with <b>Add part…</b></span>")
            return
        rows = [("Package", p.package), ("Key specs", p.specs), ("Top markings", p.markings),
                ("In stock", f"{p.qty}" if p.qty else "none logged"), ("Location", p.location)]
        body = "".join(f"<tr><td class='muted' style='padding-right:10px'>{k}</td><td>{v or '—'}</td></tr>" for k, v in rows)
        notes = f"<p>{p.notes}</p>" if p.notes else ""
        tag = " <span class='muted'>(your part)</span>" if p.user_added else ""
        link = self.db.datasheet_link(p)
        link_text = "Open datasheet" if p.datasheet_url.strip() else "Search for datasheet"
        photo = ""
        if p.photo_path:
            f = Path(p.photo_path)
            photo = (f"<p><img src='{f.as_uri()}' width='240'></p>" if f.is_file()
                     else "<p class='muted'>(the attached photo file is missing)</p>")
        mark_note = ("<p class='muted'>SMD marking codes vary between manufacturers: confirm the package "
                     "and pinout before relying on one.</p>") if p.markings else ""
        self.detail.show_html(f"<h2 style='margin:0'>{p.part_number}{tag}</h2><p>{p.description}</p>"
                              f"<table cellspacing=3>{body}</table>{notes}{photo}"
                              f"<p><a href='{link}'>{link_text} ↗</a></p>{mark_note}"
                              "<p class='muted'>Specs are typical values: confirm against the datasheet "
                              "before designing around them.</p>")

    # --------------------------------------------------------------- actions
    def add_part(self, prefill: dict | None = None, photo: str | None = None) -> int | None:
        dlg = PartDialog(self.db.categories(), None, self, prefill)
        if not prefill:
            pre = self.search.text().strip()
            if pre:
                dlg.pn.setText(pre)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        pid = self.db.add(**dlg.values())
        if photo:
            try:
                self.db.attach_photo(pid, photo)
            except ValueError as exc:
                QMessageBox.warning(self, "Photo not attached", str(exc))
        self.reload_categories()
        self.refresh(keep_id=pid)
        return pid

    def edit_part(self) -> None:
        p = self.selected_part()
        if not p:
            return
        dlg = PartDialog(self.db.categories(), p, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.update(p.id, **dlg.values())
            self.reload_categories()
            self.refresh(keep_id=p.id)

    def delete_part(self) -> None:
        p = self.selected_part()
        if not p:
            return
        if QMessageBox.question(self, "Delete part", f"Delete {p.part_number} from your library?") \
                == QMessageBox.StandardButton.Yes:
            self.db.delete(p.id)
            self.reload_categories()
            self.refresh()

    def bump(self, delta: int) -> None:
        p = self.selected_part()
        if not p:
            return
        self.db.update(p.id, qty=max(p.qty + delta, 0))
        self.refresh(keep_id=p.id)

    # ---------------------------------------------------------------- photos
    def do_attach(self, part_id: int, path: str) -> None:
        self.db.attach_photo(part_id, path)
        self.refresh(keep_id=part_id)

    def attach_photo(self) -> None:
        p = self.selected_part()
        if not p:
            QMessageBox.information(self, "Select a part", "Select a part first, then attach a photo to it.")
            return
        path, _ = QFileDialog.getOpenFileName(self, f"Photo of {p.part_number}", "", IMAGE_FILTER)
        if path:
            try:
                self.do_attach(p.id, path)
            except ValueError as exc:
                QMessageBox.warning(self, "Couldn't attach photo", str(exc))

    def remove_photo(self) -> None:
        p = self.selected_part()
        if p and p.photo_path:
            self.db.remove_photo(p.id)
            self.refresh(keep_id=p.id)

    def identify_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Photo of the component", "", IMAGE_FILTER)
        if path:
            self.run_photo_dialog(path)

    def run_photo_dialog(self, path: str) -> PhotoIdentifyDialog:
        dlg = PhotoIdentifyDialog(self.db, path, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.handle_photo_choice(dlg, path)
        return dlg

    def handle_photo_choice(self, dlg: PhotoIdentifyDialog, path: str) -> None:
        if dlg.chosen_part_id is not None:
            self.category.setCurrentText("All")
            self.in_stock.setChecked(False)
            part = self.db.get(dlg.chosen_part_id)
            if part:
                self.search.setText(part.part_number)
                self.refresh(keep_id=part.id)
        elif dlg.chosen_token and dlg.add_new:
            self.add_part({"part_number": dlg.chosen_token, "markings": dlg.chosen_token}, photo=path)
        elif dlg.chosen_token:
            self.search.setText(dlg.chosen_token)

    # ------------------------------------------------------------------- CSV
    def do_import(self, path: str):
        res = self.db.import_csv(path)
        self.reload_categories()
        self.refresh()
        return res

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import parts from CSV", "", "CSV files (*.csv *.txt)")
        if not path:
            return
        try:
            res = self.do_import(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Couldn't import", str(exc))
            return
        msg = f"Added {res.added} new part(s), updated {res.updated}, skipped {res.skipped} empty row(s)."
        if res.errors:
            msg += "\n\nNotes:\n" + "\n".join(res.errors[:6])
        QMessageBox.information(self, "Import finished", msg)

    def do_export(self, path: str, only_owned: bool) -> int:
        return self.db.export_csv(path, only_owned=only_owned)

    def export_csv(self) -> None:
        ans = QMessageBox.question(
            self, "Export parts", "Export only the parts you own (quantity above 0)?\n\n"
                                  "Choose No to export the whole library.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Cancel:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export parts", "my_parts.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            n = self.do_export(path, ans == QMessageBox.StandardButton.Yes)
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't export", str(exc))
            return
        QMessageBox.information(self, "Export finished", f"Saved {n} part(s) to {path}.")
