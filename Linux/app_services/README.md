# Linux app services

`catalog.py` ports the catalog protocol and copy validation from
`Packages/LightTouchCore/Sources/LightTouchCore/Features/CatalogClient.swift`,
`CatalogCopy.swift` and `CatalogFilter.swift`. The existing repository license
applies. It uses Python 3.9+ standard-library HTTPS with certificate verification.

The desktop UI can run `CatalogClient.search(query, target)` on a background
thread, show `incompatibility(app)` on unavailable rows, use `versions(app)` for
other copies, and call `download(app, target, private_scratch)` only after a user
selects a copy. `Target` contains the selected guest's product type, iOS version,
CPU architecture and device family; it describes the guest, not the Linux host.
Every search and copy recheck names the actual target. Downloads revalidate copy
identity, availability, architecture, encryption classification and minimum OS,
then stream to a private file and verify the archive's size/MD5. MD5 is an archive
integrity check, not a publisher signature. A progress callback receives transferred
bytes and expected size; cancellation and failed integrity checks remove the owned
partial file. The caller owns successful scratch files and must retain or remove
them when the queued installation finishes.

The catalog can be tested without downloading applications:

```sh
python3 -m unittest discover -s Linux/tests -p 'test_app_services_catalog.py' -v
```

This is the catalog component, not guest installation acceptance. The upstream
Mac installer starts a private `usbmuxd-qemu` bridge, checks device reachability,
preflights the IPA, repairs a missing executable bit in a staged copy, checks AFC
free space, uploads through AFC, and installs through `installation_proxy` with
bounded service operations. The portable Linux frontend does not yet own that
bridge or expose an installation session. Those components and actual authorized
IPA installation/launch remain required. The small typed guest-agent API is not
an IPA upload transport. Do not report a downloaded file as an installed app.
