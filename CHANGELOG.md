# Changelog

## [0.5.3](https://github.com/RayanAlyasi/cleanrr/compare/v0.5.2...v0.5.3) (2026-07-23)


### Bug Fixes

* bugs found via live deployment test ([#68](https://github.com/RayanAlyasi/cleanrr/issues/68)) ([2d37233](https://github.com/RayanAlyasi/cleanrr/commit/2d3723388908977bcacca1844fe02afc0be61baf))
* parse single admin Telegram ID from env correctly ([#66](https://github.com/RayanAlyasi/cleanrr/issues/66)) ([777f74b](https://github.com/RayanAlyasi/cleanrr/commit/777f74b9c8fd1a1350d0deb122d8e4d532c426e0))

## [0.5.2](https://github.com/RayanAlyasi/cleanrr/compare/v0.5.1...v0.5.2) (2026-07-23)


### Bug Fixes

* resolve whole-project audit findings ([#64](https://github.com/RayanAlyasi/cleanrr/issues/64)) ([4dd99ca](https://github.com/RayanAlyasi/cleanrr/commit/4dd99ca7dfbd593b863b999a4babebe5a0fc782b))

## [0.5.1](https://github.com/RayanAlyasi/cleanrr/compare/v0.5.0...v0.5.1) (2026-05-25)


### Bug Fixes

* cardinality, audit logs, lock timeout ([#58](https://github.com/RayanAlyasi/cleanrr/issues/58)) ([1d7ced9](https://github.com/RayanAlyasi/cleanrr/commit/1d7ced9a16bd04dfa875b85b6bdd8dab49da8a92))

## [0.5.0](https://github.com/RayanAlyasi/cleanrr/compare/v0.4.1...v0.5.0) (2026-05-25)


### Features

* add destructive-action confirmation flow ([#55](https://github.com/RayanAlyasi/cleanrr/issues/55)) ([38de348](https://github.com/RayanAlyasi/cleanrr/commit/38de348b347fc3a020febd500020556d770f438a))
* write tools — delete_torrent, force_research_movie, force_research_show ([#57](https://github.com/RayanAlyasi/cleanrr/issues/57)) ([269ba70](https://github.com/RayanAlyasi/cleanrr/commit/269ba7019914d2c494910b3f2ed1a6d043e05d3c))

## [0.4.1](https://github.com/RayanAlyasi/cleanrr/compare/v0.4.0...v0.4.1) (2026-05-24)


### Bug Fixes

* close link-code race; bound untrusted inputs ([#52](https://github.com/RayanAlyasi/cleanrr/issues/52)) ([9931aa8](https://github.com/RayanAlyasi/cleanrr/commit/9931aa80226f2e8beacdc353c6bb7523b3c1cade))

## [0.4.0](https://github.com/RayanAlyasi/cleanrr/compare/v0.3.1...v0.4.0) (2026-05-24)


### Features

* add fuzzy-match Overseerr request lookup ([#46](https://github.com/RayanAlyasi/cleanrr/issues/46)) ([5c216d7](https://github.com/RayanAlyasi/cleanrr/commit/5c216d7258e294421468223cdc0a18cc6c986b66))
* add MCP tool foundation + Overseerr list_my_requests ([#44](https://github.com/RayanAlyasi/cleanrr/issues/44)) ([3d732c5](https://github.com/RayanAlyasi/cleanrr/commit/3d732c533dfc1b4f5ca413f9e153eb4c9c270b53))
* add qBittorrent stalled-torrents tool ([#50](https://github.com/RayanAlyasi/cleanrr/issues/50)) ([2f38f22](https://github.com/RayanAlyasi/cleanrr/commit/2f38f2219505857dcab192a93ef699bf6bd653a6))
* add Radarr movie status tool ([#49](https://github.com/RayanAlyasi/cleanrr/issues/49)) ([85c0ae1](https://github.com/RayanAlyasi/cleanrr/commit/85c0ae1d39b77ad8f8b6ab023bddddbc5cde43d3))
* add Sonarr TV show status tool ([#48](https://github.com/RayanAlyasi/cleanrr/issues/48)) ([f3ae19e](https://github.com/RayanAlyasi/cleanrr/commit/f3ae19e9f1e7f4b14cfb9a393eb6f1c02d85359f))
* harden runtime prompt with trust hierarchy ([606bf49](https://github.com/RayanAlyasi/cleanrr/commit/606bf49576f2a71b46c58b378dd74f0267e19fbb))

## [0.3.1](https://github.com/RayanAlyasi/cleanrr/compare/v0.3.0...v0.3.1) (2026-05-20)


### Bug Fixes

* cap message length and timeout Claude SDK ([#39](https://github.com/RayanAlyasi/cleanrr/issues/39)) ([6be9aa3](https://github.com/RayanAlyasi/cleanrr/commit/6be9aa3b1d1edfe862443a72798ff2c2962e40c0))
* clear credentials on shutdown and bind metrics private ([#41](https://github.com/RayanAlyasi/cleanrr/issues/41)) ([c63a2fc](https://github.com/RayanAlyasi/cleanrr/commit/c63a2fc4dd46a97df8bc7d1a894eed838e6ea992))
* log shutdown and silence httpx token-leaking logs ([#43](https://github.com/RayanAlyasi/cleanrr/issues/43)) ([7d8d249](https://github.com/RayanAlyasi/cleanrr/commit/7d8d2499a90479f8d686dfd448988626a560d591))

## [0.3.0](https://github.com/RayanAlyasi/cleanrr/compare/v0.2.0...v0.3.0) (2026-05-20)


### Features

* add observability and graceful SDK error handling ([#36](https://github.com/RayanAlyasi/cleanrr/issues/36)) ([7a5a2f1](https://github.com/RayanAlyasi/cleanrr/commit/7a5a2f1091e2d50187f2b4bb1b0b3cddbe955d08))


### Documentation

* close gaps in python-style rule ([#34](https://github.com/RayanAlyasi/cleanrr/issues/34)) ([d9205c3](https://github.com/RayanAlyasi/cleanrr/commit/d9205c317fb8ff72371137ae41b46b2727d5bdee))

## [0.2.0](https://github.com/RayanAlyasi/cleanrr/compare/v0.1.3...v0.2.0) (2026-05-19)


### Features

* /invite and /link commands with SQLite identity store ([#31](https://github.com/RayanAlyasi/cleanrr/issues/31)) ([0a11447](https://github.com/RayanAlyasi/cleanrr/commit/0a114471951ab5a709d7f422f632834dbfe15e8b))

## [0.1.3](https://github.com/RayanAlyasi/cleanrr/compare/v0.1.2...v0.1.3) (2026-05-19)


### Bug Fixes

* **ci:** apt-get upgrade in Dockerfile to clear base-image CVEs ([#28](https://github.com/RayanAlyasi/cleanrr/issues/28)) ([0676282](https://github.com/RayanAlyasi/cleanrr/commit/0676282286cc4ee3f1ee498904e2a64d29509a8f))


### Documentation

* add logo pack and use hero banner in README ([#26](https://github.com/RayanAlyasi/cleanrr/issues/26)) ([7e8cfe4](https://github.com/RayanAlyasi/cleanrr/commit/7e8cfe4f2795965d17613ed81bc21dee0ab5f281))

## [0.1.2](https://github.com/RayanAlyasi/cleanrr/compare/v0.1.1...v0.1.2) (2026-05-18)


### Bug Fixes

* **ci:** use lowercased tag from metadata-action for Trivy ([#24](https://github.com/RayanAlyasi/cleanrr/issues/24)) ([6d2d534](https://github.com/RayanAlyasi/cleanrr/commit/6d2d53476853208ee4fd512e1edf87c1a7e71968))

## [0.1.1](https://github.com/RayanAlyasi/cleanrr/compare/v0.1.0...v0.1.1) (2026-05-18)


### Bug Fixes

* **ci:** skip codecov upload when CODECOV_TOKEN is unset ([#13](https://github.com/RayanAlyasi/cleanrr/issues/13)) ([297b951](https://github.com/RayanAlyasi/cleanrr/commit/297b95120aa8e3e985d9710242d49627fcbf3f02))
* **deps:** bump trivy-action to v0.36.0 ([#10](https://github.com/RayanAlyasi/cleanrr/issues/10)) ([7f23e06](https://github.com/RayanAlyasi/cleanrr/commit/7f23e06eb917ea54a2744de26ec55324a1966961))
