# Instance profiling

What the instance scripts install, and what a server folder still needs when its
start command assumes a mod loader.

Checked on the instance, 2026-09-27. Loader and mod versions below are that
pass. Refresh them with the same sources when the Minecraft version changes.

## What provisioning covers

`setup.sh` stops at the instance: the JDK, and one shared vanilla jar at
`/mnt/persist/minecraft/server_<version>.jar`. `provision_servers.py` then
creates the server folder, `eula.txt`, `server.properties`, the start/stop
scripts, and the systemd unit. The start command is copied unchanged from
`minecraft-servers.yaml` in AWS-Games-Config.

A start command that launches the shared vanilla jar is complete after that.
The unit can be enabled and the folder complete, and a start can still fail
when the command names a launcher the provisioner never creates.

## Mod loaders are chosen per server

Fabric is one loader a server may choose. It is not installed by `setup.sh`,
and a server that stays on the shared vanilla jar has no loader profile.
The choice lives in that server's `start_command` and in extra files inside
its folder.

## Fabric launcher profile

Use this when the start command is `-jar fabric-server-launch.jar`. That name
is the manual installer output. The single-file launcher
(`fabric-server-mc.<mc>-loader.<loader>-launcher.<installer>.jar`) is a
different start command and does not satisfy this one.

For a server folder that should run Minecraft `<version>`:

1. Confirm Java matches that version (Java 21 for 1.21.11; see the package
   note in `setup.sh`) and that
   `/mnt/persist/minecraft/server_<version>.jar` is the official Mojang server
   jar.
2. Run the current stable Fabric Installer (the universal installer jar, not
   the executable server launcher) as `ec2-user`, into the server folder,
   without `-downloadMinecraft`:

   ```bash
   java -jar fabric-installer-<installer>.jar server \
     -dir /mnt/persist/minecraft/<folder> \
     -mcversion <version> \
     -loader <stable-loader>
   ```

   Pick `<stable-loader>` from
   `https://meta.fabricmc.net/v2/versions/loader/<version>`: the newest entry
   whose loader is marked stable. For 1.21.11 that is loader 0.19.5, installed
   with installer 1.1.2.

3. Before the first start, write `fabric-server-launcher.properties` in the
   server folder:

   ```
   serverJar=../server_<version>.jar
   ```

   The launch jar reads that on startup. With the file absent it looks for
   `server.jar` in the working directory and misses the shared vanilla jar.
   Omit `-downloadMinecraft` so the installer does not fetch a second copy.

The installer rewrites `fabric-server-launch.jar` and refreshes `libraries/`
(loader, intermediary, and their jars). It leaves `server.properties`,
`eula.txt`, and the generated start/stop scripts alone. `-noprofile` is a
client-installer flag and does nothing on the server command. There is no
`.fabric/` directory until the first real launch; delete `.fabric/` only when
upgrading a server that has already run.

The worked 1.21.11 profile checks out as: manifest main class
`net.fabricmc.loader.impl.launch.server.FabricServerLauncher`, embedded
`launch.mainClass` of `net.fabricmc.loader.impl.launch.knot.KnotServer`, and a
classpath whose loader and intermediary jars exist on disk. `serverJar`
resolves to the shared vanilla jar and its sha1 matches Mojang.

## Mod profile

Mods are a further step, and only for a server that runs a loader. The
installer does not create `mods/`. Put server jars in
`/mnt/persist/minecraft/<folder>/mods/`, owned by `ec2-user`. They load on the
next start.

Decide each client mod by where its logic runs:

- Client-only mods stay off the server. MaLiLib, Litematica, MiniHUD, and
  Tweakeroo are in this set (`server_side: unsupported` on Modrinth).
- A mod whose environment is server-required belongs on the server even when
  the client also has it for singleplayer. Right Click Harvest and Tree
  Harvester are in this set. Install their required libraries with them
  (Fabric API, Architectury, and JamLib for Right Click Harvest; Collective
  for Tree Harvester).
- Xaero's Minimap and Xaero's World Map run on the client without a server
  jar. The same jar on the server is what turns on world identification:
  separate waypoint sub-worlds, and server map-selection from the server's
  level ids. Use one version on both sides. Both still require Fabric API.
  Their optional dependency, Open Parties and Claims, is not part of this
  profile.

Resolve "latest for this Minecraft version" with the Modrinth version API
filtered to Fabric and that game version, and take the newest release. A
newer mod line that no longer lists the game version is not a candidate.
Dropping a mod frees a library only when no remaining jar requires it.
Better Than Mending declares Fabric API and nothing else depends on it, so
removing it drops no other jar.

### DarkBoris, Minecraft 1.21.11

One server that chose Fabric, after that pass:

| Jar | Version |
|---|---|
| `fabric-api-0.141.6+1.21.11.jar` | 0.141.6+1.21.11 |
| `architectury-19.0.1-fabric.jar` | 19.0.1 |
| `jamlib-fabric-2.0.0+1.21.11.jar` | 2.0.0+1.21.11 |
| `rightclickharvest-fabric-4.6.1+1.21.11.jar` | 4.6.1+1.21.11 |
| `collective-1.21.11-8.32.jar` | 8.32 |
| `treeharvester-1.21.11-9.3.jar` | 9.3 |
| `xaerominimap-fabric-1.21.11-26.5.0.jar` | 26.5.0 |
| `xaeroworldmap-fabric-1.21.11-1.46.0.jar` | 1.46.0 |

The client copy of this profile moves with the server for Fabric API (from
0.141.1), JamLib (from 1.3.5), Right Click Harvest (from 4.6.0), Collective
(from 8.13), Xaero's Minimap (from 25.3.10), and Xaero's World Map (from
1.40.11). Architectury 19.0.1 and Tree Harvester 9.3 already matched. Better
Than Mending is dropped on both sides.
