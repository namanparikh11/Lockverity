# Code signing policy

- Lockverity v2.1.3 is currently unsigned.
- The Windows installer and the PE executables distributed inside the portable package are currently unsigned. The portable ZIP itself does not receive Authenticode signing.
- SignPath Foundation approval was not obtained and Lockverity has no SignPath signing integration. There is no current SignPath-signed Lockverity release.
- Committers and reviewers: Naman Parikh ([@namanparikh11](https://github.com/namanparikh11)).
- Approvers: Naman Parikh ([@namanparikh11](https://github.com/namanparikh11)).
- [Privacy policy](privacy.md).

No SignPath integration, credential, signing workflow, or definitive artifact configuration is present today. Checksums belong in generated manifests or release metadata produced from final distributable artifacts; an immutable source tag is not moved to embed a later artifact hash.
