# Generate packaging changelog entries for openSUSE distros

This repository contains various tools that are useful for maintaining openSUSE packages. All tools are currently very opinionated, but any
improvements to make them more versatile are highly welcome

## changesgen

This is typically called with the current working directory inside an osc package checkout like this:

    osc vc -m "$(changesgen)"

By default, it determines the news for this version update by comparing
the versions in the uncommitted checkout with the base version that has been
committed last. it is possible to explicitly specify these as parameters as well.

## autoup

This is an experimental script that searches on repology.org for new available
versions and tests whether updating to those would succeed to build without
any further changes. These are usually bugfix (patchlevel) version updates and
often can be submitted right away.

# Contribution Guidelines

Please send PRs or file issues on Git Hub.