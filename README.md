# Generate packaging changelog entries for openSUSE distros

This repository contains various tools that are useful for maintaining openSUSE packages. All tools are currently very opinionated, but any
improvements to make them more versatile are highly welcome


## changesgen

This is typically called with the current working directory inside an osc package checkout like this:

    osc vc -m "$(changesgen)"

By default, it determines the news for this version update by comparing
the versions in the uncommitted checkout with the last comitted base version.
You can explicitly specify these as parameters as well.

changesgen benefits from an API key for newreleases.io website which is
extracting release notes from GitHub Release as well as allows contributing
back release notes for others to consume. Store the newreleases.io API key in a
.ini style config file under `~/.config/changesgenrc` following this template:

```
[DEFAULT]
newreleases_api_key = yourapikeyhere
```


## autoup

This is an experimental script that searches on repology.org for new available
versions and tests whether updating to those would succeed to build without
any further changes. These are usually bugfix (patchlevel) version updates and
often can be submitted right away.

# Contribution Guidelines

Please send PRs or file issues on Git Hub.
