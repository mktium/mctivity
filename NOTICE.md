# Third-Party and Rights Notices

Review date: 2026-09-02. This inventory records the source candidate's boundaries; it is not a new license grant or a claim that every asset's ownership has been verified.

## Project License

The existing [LICENSE](LICENSE) contains GNU GPL version 3. It is unchanged. Do not interpret its presence as permission to redistribute third-party manuals, derived diagnostic text, fonts, logos, or trademarks.

## Project Ownership

Copyright (c) 2026 上海诣儒信息科技有限公司 for the project-developed software and company-created architecture diagrams.

The project owner confirmed on 2026-09-02 that:

- the program code copyright belongs to 上海诣儒信息科技有限公司;
- the project logo belongs to 上海诣儒信息科技有限公司 and has a trademark registration certificate;
- the architecture diagrams were created in-house by 上海诣儒信息科技有限公司.

These statements record the owner's confirmation, not an independent examination of a registration certificate. They do not change the existing license terms, grant trademark rights, or claim ownership of third-party components or manufacturer-derived material. No registration certificate or registration number is included in this source release.

## EtherCAT Runtime

The C programs use the IgH EtherCAT userspace API through `ecrt.h` and link against the separately installed `libethercat`. No IgH library, header, kernel master, or binary is included in this source tree.

IgH's documentation distinguishes the GPLv2 master from its LGPLv2.1 userspace library. See the [official documentation, section 1.2, printed page 3](https://docs.etherlab.org/ethercat/1.5/pdf/ethercat_doc.pdf) and [upstream source](https://gitlab.com/etherlab.org/ethercat). Verify the licenses of the exact installed version and any copied examples separately. The runtime library's license does not determine the license of example source code.

If distributing a binary, image, container, installer, or modified external component in the future, perform a separate license/source-availability review for everything included. This source-only inventory does not clear such packages.

## Included Content

This release excludes vendor-specific diagnostic datasets and detail dialogs. Basic device fault flags and raw status codes remain available. No manufacturer manuals are bundled. Third-party content added in future releases requires its own rights and distribution review; citing a source is not itself a license grant.

## Other Assets and Dependencies

- Python code uses the standard library; Python is installed separately.
- Build tools, Node.js for release validation, Linux/systemd, and the browser are external tools, not bundled runtimes.
- Optional browser tests use a separately installed Playwright package and browser; neither is included in the source archive.
- CSS refers to fonts installed on the client system. No font files are distributed in this source tree; installing or redistributing fonts requires their own applicable terms.
- The project logo and four architecture PNGs are present. Their company ownership and in-house diagram provenance are recorded above based on the project owner's confirmation.
- Product and manufacturer names identify compatible/tested equipment only. Software licensing does not grant trademark rights or imply certification, affiliation, or endorsement.
