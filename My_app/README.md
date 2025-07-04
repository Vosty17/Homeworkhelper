# Homework Helper AI (Flask + DeepSeek + M-Pesa)

A smart web app for Kenyan students and parents to get instant, AI-powered homework help—paid for easily with M-Pesa.

## Features

- **AI Homework Help:**  
  Type or upload a photo of your question. The app uses DeepSeek AI to generate clear, step-by-step answers for math, science, essays, and more.

- **Image & Text Support:**  
  Works with both typed questions and image uploads (JPG, PNG).

- **M-Pesa Payments:**  
  Pay per question or subscribe monthly using M-Pesa STK Push. Secure and seamless for Kenyan users.

- **User Accounts:**  
  Register/login to track your questions, payments, and subscriptions.

- **Admin & Logging:**  
  Robust logging, error handling, and database support (PostgreSQL for production, SQLite for development).

## How It Works

1. **Sign Up / Login:**  
   Create an account with your username, email, and phone number.

2. **Ask a Question:**  
   Enter your question or upload a photo.

3. **Payment:**  
   - If you’re not subscribed, pay via M-Pesa STK Push.
   - Monthly subscribers skip per-question payments.

4. **AI Response:**  
   After payment, the app sends your question (and image, if any) to DeepSeek AI via API and displays a detailed answer.

5. **Track History:**  
   View your past questions and payment status in your dashboard.

## Tech Stack

- **Backend:** Flask (Python)
- **AI Integration:** DeepSeek API
- **Payments:** M-Pesa Daraja API (STK Push)
- **Database:** PostgreSQL (production) / SQLite (dev)
- **Image Handling:** Pillow (PIL)
- **Frontend:** Jinja2 templates

## Setup & Installation

1. **Clone the repo:**
   ```
   git clone <your-repo-url>
   cd <project-folder>
   ```

2. **Create a virtual environment:**
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

4. **Set environment variables:**
   - `FLASK_SECRET_KEY`
   - `DEEPSEEK_API_KEY`
   - `MPESA_CONSUMER_KEY`
   - `MPESA_CONSUMER_SECRET`
   - `MPESA_BUSINESS_SHORTCODE`
   - `MPESA_PASSKEY`
   - `MPESA_CALLBACK_URL`
   - `DATABASE_URL` (for PostgreSQL)

5. **Run the app:**
   ```
   python app.py
   ```

## Project Structure

```
.
├── app.py
├── requirements.txt
├── README.md
├── uploads/
├── templates/
├── static/
└── ...
```

## Notes

- Only supports images up to 5MB (JPG, PNG).
- M-Pesa payments require a Safaricom line.
- DeepSeek AI API key is needed for AI responses.

## License

MIT
