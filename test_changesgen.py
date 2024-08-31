from changesgen import changes_to_text, rst_to_text


def test_strip_github_issues():
    """Test that github references at the end of a one liner PR summary are removed"""
    assert (
        changes_to_text(
            'some description :github-issue:`768` (:github-user:`apjama`)\n'
        )
        == '  * some description'
    )

    assert (
        changes_to_text('Something done here (#123)\n')
        == '  * Something done here'
    )


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
