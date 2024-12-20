from changesgen import changes_to_text, extract_update_section, rst_to_text


def test_extraction_section_trio():
    extracted_section = extract_update_section(
        '0.26.2',
        '0.27.0',
        'python-trio',
        """
- not yet included
Trio 0.27.0

  included text
Trio 0.26.2 (2024-08-08)
- no longer included
""".splitlines(),
    )

    assert 'not yet included' not in extracted_section
    assert 'included text' in extracted_section
    assert 'no longer included' not in extracted_section


def test_strip_github_issues():
    """Test that github references at the end of a one liner PR summary are removed"""
    assert (
        changes_to_text(
            'some description :github-issue:`768` (:github-user:`apjama`)\n'
        )
        == '  * some description'
    )

    assert changes_to_text('Something done here (#123)\n') == '  * Something done here'


def test_rst_to_text():
    """Handle the standard CHANGES.rst formats correctly"""
    assert (
        rst_to_text("""
`3.5.3`_ - 2024-08-01
---------------------

**Fixed**

- django-rest-framework: MoneyField does not work anymore with custom serializer fields :github-issue:`768` (:github-user:`apjama`)

**Added**

- Django 5.1 support :github-issue:`767` (:github-user:`benjaoming`)

""")
        == """  * django-rest-framework: MoneyField does not work anymore with
    custom serializer fields
  * Django 5.1 support
"""
    )

    assert (
        rst_to_text("""
1.20.0 (2024-07-19)
-------------------

* Fix the ``admin_register`` fixer to avoid rewriting when there are duplicate ``ModelAdmin`` classes in the file.

  `Issue #471 <https://github.com/link/to/471>`__.

""")
        == """  * Fix the admin_register fixer to avoid rewriting when there
    are duplicate ModelAdmin classes in the file. Issue #471.
"""
    )
