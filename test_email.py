from monitor import send_email

send_email(
    subject="RV-bot: Test af e-mail",
    text_body="Din RV-bot kan nu sende e-mails!",
    html_body="<h2>RV-bot fungerer!</h2><p>Din bot kan sende e-mails.</p>"
)

print("Testmail sendt!")
