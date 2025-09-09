import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta, timezone
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback
import uuid
import pytz
from google.cloud import firestore as google_firestore

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask app
app = Flask(__name__)

# --- Firestore Connection Setup ---
db = None
try:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
    logging.info("Firestore connected using Cloud Run environment credentials.")
    db = firestore.client()
except ValueError:
    try:
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            cred = credentials.Certificate(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'))
            firebase_admin.initialize_app(cred)
            logging.info("Firestore connected using GOOGLE_APPLICATION_CREDENTIALS.")
            db = firestore.client()
        else:
            logging.warning("No GOOGLE_APPLICATION_CREDENTIALS found. Running in mock data mode.")
    except Exception as e:
        logging.error(f"Error initializing Firebase: {e}")
        logging.error("Continuing without database connection. Using mock data.")

# Mock data for demonstration if Firebase is not connected
MOCK_PATIENTS = {
    'Tahmina Akhtar': {
        'name': 'Tahmina',
        'surname': 'Akhtar',
        'dateOfBirth': '1992-03-12',
        'insuranceProvider': 'MedStar Health',
        'policyNumber': 'D123456',
        'email': 'tahmina.akhtar2807@gmail.com',
        'bookings': [],
    }
}

MOCK_DOCTORS = {
    'gp-001': {
        'id': 'gp-001',
        'name': 'Dr. Lucy Morgan, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'New York',
        'availability': {
            '2025-09-07': ["13:00", "14:00"]
        }
    },
    'gp-002': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            '2025-09-07': [],
            '2025-09-08': ["09:00", "10:00", "11:00", "12:00", "13:00"]
        }
    }
}

INSURANCE_RATES = {
    "MedStar Health": {"appointment_cost": 150.00, "co_pay": 25.00},
    "Blue Cross Blue Shield": {"appointment_cost": 180.00, "co_pay": 30.00},
    "default": {"appointment_cost": 200.00, "co_pay": 50.00}
}

# --- Core Business Logic Functions ---
def calculate_appointment_cost(insurance_provider: str) -> dict:
    """
    Calculates the appointment cost and patient's co-pay based on insurance.
    """
    rates = INSURANCE_RATES.get(insurance_provider, INSURANCE_RATES["default"])
    total_cost = rates["appointment_cost"]
    co_pay = rates["co_pay"]
    
    return {
        "totalCost": total_cost,
        "patientCopay": co_pay,
        "insuranceClaim": total_cost - co_pay
    }


def send_email_to_patient(email: str, booking_details: dict):
    """
    Sends a confirmation email using a secure SMTP connection.
    NOTE: You must configure an application-specific password for your email account
    and store it securely. Do NOT hardcode your main email password here.
    """
    smtp_server = os.environ.get('SMTP_SERVER', "smtp.gmail.com")
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_EMAIL', "niljoshna28@gmail.com")
    password = os.environ.get('SMTP_PASSWORD', "nxlcscihekyxcedc")

    if sender_email == "your_email@gmail.com" or password == "your_app_password":
        logging.warning("SMTP configuration not set via environment variables. Cannot send email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Appointment Confirmation"
    msg["From"] = sender_email
    msg["To"] = email

    # Create the plain-text and HTML versions of your message
    cost_breakdown = booking_details.get('costBreakdown', {})
    total_cost = cost_breakdown.get('totalCost', 0)
    patient_copay = cost_breakdown.get('patientCopay', 0)

    text_content = f"""
    Appointment Confirmation
    Hello,
    Your appointment has been successfully booked with {booking_details.get('doctorName')} on {booking_details.get('appointmentDate')} at {booking_details.get('appointmentTime')}.
    
    Booking Details:
    - Appointment Type: {booking_details.get('bookingType')}
    - Doctor: {booking_details.get('doctorName')}
    - Date: {booking_details.get('appointmentDate')}
    - Time: {booking_details.get('appointmentTime')}
    
    Cost Breakdown:
    - Total Appointment Cost: ${total_cost:.2f}
    - Your Co-pay (After Insurance Claim): ${patient_copay:.2f}
    
    We look forward to seeing you!
    
    Best regards,
    The Clinic Team
    """

    html_content = f"""
    <html>
    <body style="font-family: sans-serif; line-height: 1.6;">
        <h2>Appointment Confirmation</h2>
        <p>Hello,</p>
        <p>Your appointment has been successfully booked with <b>{booking_details.get('doctorName')}</b> on <b>{booking_details.get('appointmentDate')}</b> at <b>{booking_details.get('appointmentTime')}</b>.</p>
        <h3>Booking Details:</h3>
        <ul>
            <li><b>Appointment Type:</b> {booking_details.get('bookingType')}</li>
            <li><b>Doctor:</b> {booking_details.get('doctorName')}</li>
            <li><b>Date:</b> {booking_details.get('appointmentDate')}</li>
            <li><b>Time:</b> {booking_details.get('appointmentTime')}</li>
        </ul>
        <h3>Cost Breakdown:</h3>
        <ul>
            <li><b>Total Appointment Cost:</b> ${total_cost:.2f}</li>
            <li><b>Your Co-pay (After Insurance Claim):</b> ${patient_copay:.2f}</li>
        </ul>
        <p>We look forward to seeing you!</p>
        <p>Best regards,<br>The Clinic Team</p>
    </body>
    </html>
    """

    part1 = MIMEText(text_content, "plain")
    part2 = MIMEText(html_content, "html")
    msg.attach(part1)
    msg.attach(part2)

    try:
        # Create a secure SSL context and a connection
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.sendmail(sender_email, email, msg.as_string())
            logging.info(f"Email sent successfully via SMTP to {email}")
    except Exception as e:
        logging.error(f"Failed to send email via SMTP: {e}"))

def find_available_doctors(specialty, location, date_str=None):
    available_doctors = []
    if db:
        docs_ref = db.collection('doctors')
        docs = docs_ref.where(
            filter=firestore.FieldFilter('specialty', '==', specialty.title())
        ).where(
            filter=firestore.FieldFilter('city', '==', location.title())
        ).stream()

        for doc in docs:
            doctor_data = doc.to_dict()
            availability_map = doctor_data.get('availability', {})
            valid_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d, times in availability_map.items() if times]
            if valid_dates:
                earliest_date = min(valid_dates)
                doctor_data['earliest_date'] = earliest_date
                available_doctors.append(doctor_data)
    else:
        for doc in MOCK_DOCTORS.values():
            if doc['specialty'].lower() == specialty.lower() and doc['city'].lower() == location.lower():
                availability_map = doc.get('availability', {})
                valid_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d, times in availability_map.items() if times]
                if valid_dates:
                    earliest_date = min(valid_dates)
                    doc['earliest_date'] = earliest_date
                    available_doctors.append(doc)

    available_doctors.sort(key=lambda d: d.get('earliest_date'))
    return available_doctors

# --- Webhook Endpoints ---
@app.route('/')
def home():
    return "Webhook is running successfully!"

@app.route('/webhook', methods=['POST'])
def webhook():
    logging.info("--- Webhook Request Received ---")
    request_data = request.get_json()
    logging.info(f"Full Request JSON: {request_data}")

    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')

    response_text = "I'm sorry, an error occurred. Please try again."

    # --- Search Doctors ---
    if tag == 'search_doctors':
        specialty = parameters.get('specialty')
        location_param = parameters.get('location')
        location = location_param.get('city') if isinstance(location_param, dict) else location_param

        if not specialty or not location:
            response_text = "I'm missing some information. Please provide your preferred specialty and location."
        else:
            try:
                available_doctors = find_available_doctors(specialty, location)

                if available_doctors:
                    response_text_list = ["Here are the doctors I found:"]
                    tomorrow = datetime.now(pytz.utc).date() + timedelta(days=1)

                    for i, doc in enumerate(available_doctors[:5]):
                        response_text_list.append(f"\n{i+1}. {doc['name']} ({doc['specialty']}, {doc['city']})")
                        availability_map = doc.get('availability', {})
                        shown_dates = 0

                        for date_str, times in sorted(availability_map.items()):
                            try:
                                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                            except ValueError:
                                continue

                            if date_obj >= tomorrow and times:
                                response_text_list.append(f"    - On {date_str}:")
                                for time_slot in times:
                                    response_text_list.append(f"        - {time_slot}")
                                shown_dates += 1
                                if shown_dates >= 3:
                                    break
                    response_text = "\n".join(response_text_list)
                else:
                    response_text = f"I could not find any {specialty} doctors in {location} with availability."
            except Exception as e:
                logging.error(f"Error searching for doctors: {e}")
                logging.error(traceback.format_exc())
                response_text = "I am having trouble looking for doctors right now. Please try again later."

    # --- Collect Patient Info ---
    elif tag == 'collect_patient_info':
        try:
            doctor_name = parameters.get('doctor_name')
            appointment_time = parameters.get('appointment_time')
            appointment_date = parameters.get('appointment_date')

            if not doctor_name or not appointment_time or not appointment_date:
                response_text = "I'm sorry, I seem to have lost the appointment details. Please try selecting a time again."
            else:
                custom_payload = {
                    "doctorName": doctor_name,
                    "appointmentTime": appointment_time,
                    "appointmentDate": appointment_date
                }

                return jsonify({
                    'fulfillmentResponse': {
                        'messages': [
                            {
                                'text': {
                                    'text': ["You're booking with " + doctor_name['original'] + " on " +
                                            str(int(appointment_date['year'])) + "/" +
                                            str(int(appointment_date['month'])) + "/" +
                                            str(int(appointment_date['day'])) + " at " +
                                            str(int(appointment_time['hours'])) + ":" +
                                            str(int(appointment_time['minutes'])) +
                                            ". Let's collect your details.\nPlease provide your personal and insurance details to complete the booking."]
                                }
                            },
                            {
                                'payload': {
                                    'customPayload': custom_payload
                                }
                            }
                        ]
                    }
                })
        except Exception as e:
            logging.error(f"Error in collect_patient_info: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble with the next step. Please try again later."

    # --- Confirm Cost ---
    elif tag == 'ConfirmCost':
        try:
            patient_name_param = parameters.get('name')
            dob_param = parameters.get('dateofbirth')
            insurance_provider = parameters.get('insuranceprovider')
            policy_number = parameters.get('policynumber')
            specialty = parameters.get('specialty')
            location = parameters.get('location')
            doctor_name = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time = parameters.get('appointment_time')

            if not all([patient_name_param, dob_param, insurance_provider, policy_number, specialty, location, doctor_name, appointment_date_param, appointment_time]):
                response_text = "I'm missing some information to complete your booking. Please provide all details."
            else:
                cost_details = calculate_appointment_cost(insurance_provider)
                patient_data = MOCK_PATIENTS.get(patient_name_param.get('original'))
                if patient_data:
                    booking_details = {
                        "bookingId": str(uuid.uuid4()),
                        "bookingType": "appointment",
                        "doctorName": doctor_name['original'],
                        "specialty": specialty,
                        "appointmentDate": f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}",
                        "appointmentTime": f"{appointment_time['hours']}:{appointment_time['minutes']}",
                        "costBreakdown": cost_details,
                        "status": "confirmed"
                    }
                    send_email_to_patient(patient_data['email'], booking_details)
                    response_text = f"Success! Your booking has been confirmed with {doctor_name['original']}. The total cost is ${cost_details['totalCost']:.2f} with a patient co-pay of ${cost_details['patientCopay']:.2f}. An email has been sent to your registered address."
                else:
                    response_text = "I could not find a patient with the provided details to confirm your booking. Please check your information."

        except Exception as e:
            logging.error(f"Error during ConfirmCost process: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble confirming your booking right now. Please try again later."

    # --- Book Appointment ---
    elif tag == 'book_appointment':
        try:
            patient_name_param = parameters.get('name')
            dob_param = parameters.get('dateofbirth')
            insurance_provider = parameters.get('insuranceprovider')
            policy_number = parameters.get('policynumber')
            specialty = parameters.get('specialty')
            doctor_name_param = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time_param = parameters.get('appointment_time')

            if not all([patient_name_param, dob_param, insurance_provider, policy_number, specialty, doctor_name_param, appointment_date_param, appointment_time_param]):
                response_text = "I'm missing some information to complete your booking. Please provide all details."
            else:
                appointment_date = f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}"
                appointment_time = f"{appointment_time_param['hours']}:{appointment_time_param['minutes']}"
                doctor_name = doctor_name_param['original']
                patient_full_name = patient_name_param['original']

                name_parts = patient_full_name.split(' ', 1)
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ''

                patient_data = MOCK_PATIENTS.get(patient_full_name)
                if patient_data:
                    doctor_data = next((doc for doc in MOCK_DOCTORS.values() if doc['name'] == doctor_name), None)
                    if doctor_data:
                        availability_list = doctor_data.get('availability', {}).get(appointment_date, [])
                        if appointment_time in availability_list:
                            availability_list.remove(appointment_time)
                            MOCK_DOCTORS[doctor_data['id']]['availability'][appointment_date] = availability_list
                            cost_details = calculate_appointment_cost(insurance_provider)
                            booking_details = {
                                "bookingId": str(uuid.uuid4()),
                                "bookingType": "appointment",
                                "doctorName": doctor_name,
                                "specialty": specialty,
                                "appointmentDate": appointment_date,
                                "appointmentTime": appointment_time,
                                "costBreakdown": cost_details,
                                "status": "confirmed"
                            }
                            patient_data['bookings'].append(booking_details)
                            send_email_to_patient(patient_data['email'], booking_details)
                            response_text = f"Success! Your booking with {doctor_name} on {appointment_date} at {appointment_time} has been confirmed. The total cost is ${cost_details['totalCost']:.2f} with a patient co-pay of ${cost_details['patientCopay']:.2f}. An email has been sent to your registered address."
                        else:
                            response_text = "The selected time slot is not available. Please choose from the list of available times."
                    else:
                        response_text = "The doctor you selected could not be found in our records."
                else:
                    response_text = "I could not find a patient with the provided details. Please try again."

        except Exception as e:
            logging.error(f"Error during book_appointment process: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble completing your booking right now. Please try again later."

    logging.info(f"Sending response to Dialogflow: {response_text}")
    return jsonify({
        'fulfillmentResponse': {
            'messages': [{'text': {'text': [response_text]}}]
        }
    })

if __name__ == '__main__':
    logging.info("Starting application locally...")
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
