# Cost freeze — EBS snapshot & revive notes (2026-07-26)

**Audience:** Anyone returning to this repo to bring Minecraft hosting back up after the 2026-07 cost-culling freeze.  
**Region:** `ap-southeast-4` (Melbourne).  
**Account (at freeze):** `613737894147`.

---

## What happened

For balance recovery, the **ephemeral game server stack** was torn down. World data was **not** left only on a live gp3 volume long-term; a full freeze snapshot was taken before finishing persistent teardown.

| Item | Value |
|------|--------|
| Snapshot ID | **`snap-05f005f4d4b9d8048`** |
| Name tag | `minecraft-SurviveOrDie-2026-07-26` |
| Size | **20 GiB** (volume had been grown from the template default 10) |
| State at capture | `completed` / 100% |
| Source volume (at snap time) | `vol-0f4cee5cb4bc42932` |
| AZ | **`ap-southeast-4c`** (volume from snap must be created here) |
| World / server tags | `SurviveOrDie`, Minecraft `1_20_4` |
| Description | `Minecraft SurviveOrDie world freeze 2026-07-26 (pre GamePersistentStack teardown)` |

An older 10 GiB snapshot (`backupppp`, Apr 2026) was **deleted** after the 20 GiB freeze snap completed — do not look for it.

**EIP at freeze (may be released later for cost):** `16.26.227.159` / `eipalloc-018f9c27d9bfa748e`. A new persistent stack create will allocate a **new** public IP unless the template is taught to re-adopt a retained EIP.

---

## Stack model (reminder)

```
GamePersistentStack     ← EIP + EBS (persistent-resources.yaml); exports VolumeId / AllocationId
GameStack-*             ← EC2 + attachment + SG; imports those exports
```

Game stack was deleted first (allowed once imports were free). **`GamePersistentStack`** may still exist or may have been deleted afterward; both EIP and volume use **`DeletionPolicy: Retain`**, so stack delete alone does **not** remove them — manual delete is required for cost to drop to **snapshot storage only**.

---

## Critical: current template cannot restore from this snapshot

`persistent-resources.yaml` creates a **blank** volume (`Size` / `VolumeType` / `AvailabilityZone` only). There is **no `SnapshotId`** (and no “existing volume id”) parameter.

So:

- `create-stack` / `setup_persistent_stack.py` with the **current** template → **new empty volume**, not SurviveOrDie.
- Revive from freeze is **plausible and intended**, but needs a **later dev cycle** on the persistent stack (and possibly `bin/setup_persistent_stack.py`).

### Later template work (when you revive)

1. Add optional **`SnapshotId`** (or equivalent) on `AWS::EC2::Volume`, with size ≥ snapshot (20 GiB).  
2. Create volume in **`ap-southeast-4c`**.  
3. Optionally support **re-import / retain EIP** if a stable IP is still wanted.  
4. Wire game stack as today via `Fn::ImportValue` exports.  
5. Document the one-liner / script path: snap → volume → persistent stack → `reinstall_stack.py`.

Until that lands, manual revive is still fine:

```bash
# Example — create volume from freeze snap (same AZ as original)
aws ec2 create-volume \
  --region ap-southeast-4 \
  --availability-zone ap-southeast-4c \
  --snapshot-id snap-05f005f4d4b9d8048 \
  --volume-type gp3 \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Purpose,Value=MinecraftServerWorldFiles},{Key=MinecraftWorldFolderName,Value=SurviveOrDie}]'
# Then adopt volume (+ EIP) into GamePersistentStack via template import / updated template, then reinstall game stack.
```

---

## Cost posture while frozen

| Keep | Typical cost driver |
|------|---------------------|
| Snapshot only | Cheap S3-backed snap storage |
| Live volume without instance | gp3 provisioned storage (avoid for long freeze) |
| Unassociated EIP | Idle EIP charge (release if not needed) |

Preferred long freeze: **snapshot retained**; live volume and EIP deleted after snap verified; template/script work deferred until next play cycle.

---

## Related

- Template: [`persistent-resources.yaml`](../persistent-resources.yaml)  
- Setup: [`bin/setup_persistent_stack.py`](../bin/setup_persistent_stack.py)  
- Personal KB culling note (optional context): `financial/budgeting/spending-kinds/aws.md` in the personal knowledge base  
