# Changelog

## [0.7.1](https://github.com/RayanAlyasi/cleanrr/compare/v0.7.0...v0.7.1) (2026-08-08)


### Documentation

* split README into focused files, clarify config groups ([#95](https://github.com/RayanAlyasi/cleanrr/issues/95)) ([9f03b3e](https://github.com/RayanAlyasi/cleanrr/commit/9f03b3e9342e405193a4056399ff74e1aba5f3b6))

## [0.7.0](https://github.com/RayanAlyasi/cleanrr/compare/v0.6.0...v0.7.0) (2026-08-08)


### Features

* sign release images with cosign (keyless) ([#84](https://github.com/RayanAlyasi/cleanrr/issues/84)) ([b124ef6](https://github.com/RayanAlyasi/cleanrr/commit/b124ef6c51d6b02a6de978730bae09672d3766e9))


### Bug Fixes

* add signoff to release-please's generated commits ([#90](https://github.com/RayanAlyasi/cleanrr/issues/90)) ([b4219ce](https://github.com/RayanAlyasi/cleanrr/commit/b4219ceb7ca7a99233232540440423d242369e74))
* add top-level permissions to codeql.yml, release.yml ([#82](https://github.com/RayanAlyasi/cleanrr/issues/82)) ([8cc7832](https://github.com/RayanAlyasi/cleanrr/commit/8cc78325ac1da47b8fd0bb3a44caddf020e592aa))
* enable pyright strict mode ([#92](https://github.com/RayanAlyasi/cleanrr/issues/92)) ([f528a1b](https://github.com/RayanAlyasi/cleanrr/commit/f528a1b876030c9a2e73a7310bb64e402e654ff3))
* harden CI per OpenSSF Scorecard findings ([#81](https://github.com/RayanAlyasi/cleanrr/issues/81)) ([08bd602](https://github.com/RayanAlyasi/cleanrr/commit/08bd602786b8b9bd34a2f7d559a0d18cd6fffa53))
* make semgrep job fail closed on scan errors ([#79](https://github.com/RayanAlyasi/cleanrr/issues/79)) ([267e089](https://github.com/RayanAlyasi/cleanrr/commit/267e0890a3d5d69793ab552c877d1fb0e607d74e))
* scope release-please.yml write permission to job level ([#83](https://github.com/RayanAlyasi/cleanrr/issues/83)) ([487a44d](https://github.com/RayanAlyasi/cleanrr/commit/487a44df06df91a8f1121a1db00d009b33c7a651))
* strip pip from the runtime image ([#85](https://github.com/RayanAlyasi/cleanrr/issues/85)) ([2b97a67](https://github.com/RayanAlyasi/cleanrr/commit/2b97a6765bcb60cdcd6f005f3c7181f546dcdc87))


### Documentation

* add OpenSSF Best Practices badge ([#87](https://github.com/RayanAlyasi/cleanrr/issues/87)) ([6d556f8](https://github.com/RayanAlyasi/cleanrr/commit/6d556f8d1966fe79a3dfb018362cdf198f067d2d))
* add THREAT_MODEL.md, close OSPS-SA-03.01 ([#89](https://github.com/RayanAlyasi/cleanrr/issues/89)) ([c0353bb](https://github.com/RayanAlyasi/cleanrr/commit/c0353bbfe085ebbb8a14018c7066aa794fef807b))
* close doc-only OSPS baseline-3 gaps ([#91](https://github.com/RayanAlyasi/cleanrr/issues/91)) ([8d74e84](https://github.com/RayanAlyasi/cleanrr/commit/8d74e847792e54644eb26b5dab1d38da090bc997))
* fix stale AgentPool references ([#94](https://github.com/RayanAlyasi/cleanrr/issues/94)) ([a5de202](https://github.com/RayanAlyasi/cleanrr/commit/a5de202a762a9c4a6cdf663036207e3dee3c6bc7))
* Scorecard badge + highly-visible destructive-action warning ([#86](https://github.com/RayanAlyasi/cleanrr/issues/86)) ([be8c371](https://github.com/RayanAlyasi/cleanrr/commit/be8c371a7c3d7b5c6fd178e53f22fa400eedd174))
* trim README badge row to 6 ([#93](https://github.com/RayanAlyasi/cleanrr/issues/93)) ([e94e26b](https://github.com/RayanAlyasi/cleanrr/commit/e94e26b7107bf323231a3623af7e3e0bf0df8b12))

## [0.6.0](https://github.com/RayanAlyasi/cleanrr/compare/v0.5.3...v0.6.0) (2026-08-07)


### Features

* agent-per-user pool (fixes cross-user confirmation blocking) ([#73](https://github.com/RayanAlyasi/cleanrr/issues/73)) ([3fa890a](https://github.com/RayanAlyasi/cleanrr/commit/3fa890a4cef850b07eb36dab1ccae7e165922400))


### Bug Fixes

* expose request_id in list_my_requests/find_my_request ([#76](https://github.com/RayanAlyasi/cleanrr/issues/76)) ([272a671](https://github.com/RayanAlyasi/cleanrr/commit/272a671c4eae38495f73ca535b44d6839c8dcccf))

## [0.5.3](https://github.com/RayanAlyasi/cleanrr/compare/v0.5.2...v0.5.3) (2026-07-25)


### Bug Fixes

* bound formatter latency, split handlers.py tests ([#71](https://github.com/RayanAlyasi/cleanrr/issues/71)) ([1068214](https://github.com/RayanAlyasi/cleanrr/commit/1068214864739e2f87d9d0724de86bfd4b680b66))
* bugs found via live deployment test ([#68](https://github.com/RayanAlyasi/cleanrr/issues/68)) ([2d37233](https://github.com/RayanAlyasi/cleanrr/commit/2d3723388908977bcacca1844fe02afc0be61baf))
* parse single admin Telegram ID from env correctly ([#66](https://github.com/RayanAlyasi/cleanrr/issues/66)) ([777f74b](https://github.com/RayanAlyasi/cleanrr/commit/777f74b9c8fd1a1350d0deb122d8e4d532c426e0))
* research-grounded audit findings + poster disambiguation ([#69](https://github.com/RayanAlyasi/cleanrr/issues/69)) ([349ddd6](https://github.com/RayanAlyasi/cleanrr/commit/349ddd6d08e6df4d8687899350eb18fcfefdc71e))

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
