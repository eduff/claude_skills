# Safely deleting restricted data on macOS

Removing a file on macOS does not remove the data. Six places retain it, and
Time Machine is only one:

| Retention point | Lifetime | Cleared by |
|---|---|---|
| Trash | Until emptied | Empty Trash |
| APFS **local snapshots** | ~24 h, longer if disk is roomy | `tmutil deletelocalsnapshots` |
| Time Machine backup disk | Indefinite | `tmutil delete`, or the Time Machine UI |
| iCloud Drive "Recently Deleted" | 30 days | iCloud.com or Finder |
| Versions store (`/.DocumentRevisions-V100`) | Indefinite | Delete all versions of the document |
| Quick Look / Spotlight caches | Until rebuilt | `qlmanage -r cache`, `mdutil -E` |

Local snapshots are the one people miss: macOS takes them **hourly on the
internal drive whether or not a backup disk is attached**, so a file deleted an
hour ago is very likely still readable.

## Order matters

Stop backups *before* deleting. If a backup runs between your delete and your
snapshot purge, the file is captured again and you are back where you started.

### 1. Stop Time Machine capturing more copies

```bash
tmutil addexclusion -p /path/to/restricted     # exclude from future backups
sudo tmutil disable                            # stop automatic backups
tmutil isexcluded /path/to/restricted          # confirm
```

### 2. Plan and delete the live files

Use the toolkit so the deletion is recorded:

```bash
DC=~/code/data-compliance/bin/data-compliance
$DC --profile ukb purge plan /path/to/restricted -m purge.json
$DC purge execute -m purge.json --mode delete --log purge.jsonl            # dry run
$DC purge execute -m purge.json --mode delete --log purge.jsonl --execute
```

**Do not use `--mode quarantine` on a Mac when the goal is removal.** Moving a
file does not remove it from Time Machine or from a local snapshot, and it
leaves a second live copy.

### 3. Empty the Trash

```bash
rm -rf ~/.Trash/*
```

Also check per-volume trash on any external disk: `/Volumes/<disk>/.Trashes`.

### 4. Delete APFS local snapshots

```bash
tmutil listlocalsnapshots /
sudo tmutil deletelocalsnapshots <yyyy-mm-dd-hhmmss>   # repeat per snapshot
```

Snapshots taken *before* the file existed are harmless, but deleting all of them
is simpler than reasoning about which contain it.

### 5. Delete it from the Time Machine backups

Terminal needs **Full Disk Access** (System Settings → Privacy & Security) for
`tmutil` to touch the backup volume.

```bash
tmutil listbackups                     # every backup on the attached disk
# remove one path from one backup:
sudo tmutil delete -d /Volumes/<BackupDisk> -t <backup-timestamp> -p <path-within-backup>
```

A path usually appears in many backups, so loop over `tmutil listbackups`. The
graphical route is often less error-prone: enter Time Machine, navigate to the
item, then the ⚙︎ menu → **Delete All Backups of "<item>"**.

If the file is widespread across years of backups and the backup set is not
otherwise precious, erasing and re-starting the backup disk is faster and more
certain than surgical deletion.

### 6. Caches

```bash
qlmanage -r cache        # Quick Look thumbnails, which can render spreadsheet content
sudo mdutil -E /         # rebuild the Spotlight index
```

### 7. Verify, then re-enable backups

```bash
$DC --profile ukb scan /path/that/used/to/hold/it
$DC purge certificate --log purge.jsonl > destruction-record.md
sudo tmutil enable
```

## Does the data need shredding?

No. `srm` was removed from macOS, and overwriting a file on an SSD does not
reliably overwrite the flash cells — wear levelling writes elsewhere. The
supported answer on modern Macs is **FileVault**: with full-disk encryption on,
an unlinked file is unrecoverable without the volume key. Confirm with
`fdesetup status`, and make sure the *backup disk* is encrypted too — an
unencrypted Time Machine drive is the weakest link in this whole chain.

## iCloud Drive

If the path is inside iCloud Drive (including Desktop and Documents when
"Desktop & Documents Folders" sync is on), deleting locally moves it to iCloud's
Recently Deleted for **30 days**. Empty it at
[icloud.com/drive](https://www.icloud.com/drive) → Recently Deleted → Delete All,
or in Finder's iCloud Drive sidebar. Until then the data is on Apple's servers,
which for restricted data is the more serious exposure of the two.
