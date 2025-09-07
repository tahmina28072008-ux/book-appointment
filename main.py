import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback
import uuid
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask app
app = Flask(__name__)

# --- Firestore Connection Setup ---
db = None
try:
    # On Cloud Run, credentials are automatically provided by the environment.
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
    logging.info("Firestore connected using Cloud Run environment credentials.")
    db = firestore.client()
except ValueError:
    # If running locally, you'll need a service account JSON file.
    # Set the 'GOOGLE_APPLICATION_CREDENTIALS' environment variable to its file path.
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
        logging.warning("Continuing without database connection. Using mock data.")

# Mock data for demonstration if Firebase is not connected
MOCK_PATIENTS = {
    'Tahmina': {
        'name': 'Tahmina',
        'dateOfBirth': '1992-03-12',
        'insuranceProvider': 'MedStar Health',
        'policyNumber': 'D123456',
        'email': 'tahmina.akhtar2807@gmail.com',
        'bookings': [],
    }
}

MOCK_DOCTORS = {
    'Dr. Lucy Morgan, MRCGP': {
        'id': 'gp-001',
        'name': 'Dr. Lucy Morgan, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'New York',
        'availability': {
            '2025-09-07': [
                "13:00",
                "14:00"
            ]
        }
    },
    'Dr. Adam Collins, MRCGP': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            '2025-09-07': [],
            '2025-09-08': [
                "09:00",
                "10:00",
                "14:00"
            ]
        }
    }
}

# Mock data for insurance cost calculation
INSURANCE_RATES = {
    "MedStar Health": {
        "appointment_cost": 150.00,
        "co_pay": 25.00
    },
    "Blue Cross Blue Shield": {
        "appointment_cost": 180.00,
        "co_pay": 30.00
    },
    "default": {
        "appointment_cost": 200.00,
        "co_pay": 50.00
    }
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
        logging.error(f"Failed to send email via SMTP: {e}")


def find_available_doctors(specialty, location, date_str):
    """
    Helper function to search for available doctors and their times.
    """
    available_doctors = []
    if db:
        docs_ref = db.collection('doctors')
        docs = docs_ref.where(filter=firestore.FieldFilter('specialty', '==', specialty)).where(filter=firestore.FieldFilter('city', '==', location)).stream()
        for doc in docs:
            doctor_data = doc.to_dict()
            availability_map = doctor_data.get('availability', {})
            if date_str in availability_map and isinstance(availability_map[date_str], list):
                if availability_map[date_str]:
                    available_doctors.append(doctor_data)
            else:
                logging.warning(f"Skipping doctor {doctor_data.get('name')} due to invalid availability data for date {date_str}. Expected a list of strings.")
    else: # Mock data fallback
        for doc in MOCK_DOCTORS.values():
            if doc['specialty'] == specialty and doc['city'] == location:
                availability_map = doc.get('availability', {})
                if date_str in availability_map and isinstance(availability_map[date_str], list):
                    if availability_map[date_str]:
                        available_doctors.append(doc)
    return available_doctors


# --- Webhook Endpoints ---
@app.route('/')
def home():
    """Returns a simple message to confirm the service is running."""
    return "Webhook is running successfully!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Dialogflow CX webhook for doctor availability and booking.
    """
    logging.info("--- Webhook Request Received ---")
    request_data = request.get_json()
    logging.info(f"Full Request JSON: {request_data}")
    
    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')
    
    response_text = "I'm sorry, an error occurred. Please try again."
    
    if tag == 'search_doctors':
        specialty = parameters.get('specialty')
        location_param = parameters.get('location')
        if isinstance(location_param, dict):
            location = location_param.get('city')
        else:
            location = location_param
        
        date_param = parameters.get('date')
        if date_param:
            if isinstance(date_param, str):
                date_str = date_param.split('T')[0]
            else:
                response_text = "I couldn't understand the date provided. Please try again."
                return jsonify({'fulfillmentResponse': {'messages': [{'text': {'text': [response_text]}}]}})
        else:
            # If no date is provided, use the current date
            date_str = datetime.now(pytz.timezone('Europe/London')).strftime('%Y-%m-%d')
            logging.info(f"No date provided. Using current date: {date_str}")
        
        if not specialty or not location:
            response_text = "I'm missing some information. Please provide your preferred specialty and location."
        else:
            try:
                requested_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                today = datetime.now(pytz.timezone('Europe/London')).date()
                
                if requested_date < today:
                    response_text = "I can only check for future appointments. Please provide a date that isn't in the past."
                else:
                    # Initial search for the requested date
                    available_doctors = find_available_doctors(specialty, location, date_str)
                    
                    if not available_doctors:
                        found_next_date = False
                        for i in range(1, 8):
                            next_date = requested_date + timedelta(days=i)
                            next_date_str = next_date.strftime('%Y-%m-%d')
                            available_doctors = find_available_doctors(specialty, location, next_date_str)
                            if available_doctors:
                                response_text_list = [f"I couldn't find any appointments for {requested_date.strftime('%B %d, %Y')}. However, I found some for the next available date, which is {next_date.strftime('%B %d, %Y')}. Would you like to proceed with this date?"]
                                found_next_date = True
                                for i, doc in enumerate(available_doctors):
                                    response_text_list.append(f"\n{i+1}. {doc['name']}, {doc['specialty']}")
                                    response_text_list.append(f"    - Available times: {', '.join(doc['availability'][next_date_str])}")
                                response_text = "\n".join(response_text_list)
                                break
                    
                        if not found_next_date:
                            response_text = f"I could not find any {specialty} doctors in {location} available on or after {requested_date.strftime('%B %d, %Y')}. Would you like to check a different date or location?"
                    else:
                        response_text_list = []
                        for i, doc in enumerate(available_doctors):
                            response_text_list.append(f"\n{i+1}. {doc['name']}, {doc['specialty']}")
                            response_text_list.append(f"    - Available times: {', '.join(doc['availability'][date_str])}")
                        response_text = f"Here are the available doctors and their times for {requested_date.strftime('%B %d, %Y')}:\n" + "\n".join(response_text_list)
            except Exception as e:
                logging.error(f"Error searching for doctors: {e}")
                logging.error(traceback.format_exc())
                response_text = "I am having trouble looking for doctors right now. Please try again later."
    
    # This section now handles the full booking confirmation and database update
    elif tag == 'book_appointment':
        try:
            # Extract all session parameters
            patient_name = parameters.get('patient_name')
            dob = parameters.get('dob')
            insurance_provider = parameters.get('insurance_provider')
            policy_number = parameters.get('policy_number')
            specialty = parameters.get('specialty')
            doctor_name = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time = parameters.get('appointment_time')

            if isinstance(appointment_date_param, dict):
                appointment_date = appointment_date_param.get('date_time', '').split('T')[0]
            else:
                appointment_date = appointment_date_param.split('T')[0]

            if not all([patient_name, dob, insurance_provider, policy_number, specialty, doctor_name, appointment_date, appointment_time]):
                response_text = "I'm missing some information to complete your booking. Please provide all details."
            else:
                if db:
                    # Use Firestore transaction to ensure atomic booking
                    @firestore.transactional
                    def book_and_update_in_transaction(transaction, doctor_ref, patient_ref):
                        # Get the latest data for the doctor and patient within the transaction
                        doctor_doc = doctor_ref.get(transaction=transaction)
                        patient_doc = patient_ref.get(transaction=transaction)
                        
                        if not doctor_doc.exists:
                            raise ValueError("Doctor not found.")
                        if not patient_doc.exists:
                            raise ValueError("Patient not found.")
                        
                        doctor_data = doctor_doc.to_dict()
                        patient_data = patient_doc.to_dict()
                        
                        availability_list = doctor_data.get('availability', {}).get(appointment_date, [])
                        if appointment_time not in availability_list:
                            raise ValueError("Time slot is no longer available.")
                            
                        # Remove the booked time from the array
                        availability_list.remove(appointment_time)
                        update_path = f"availability.{appointment_date}"
                        transaction.update(doctor_ref, {update_path: availability_list})
                        
                        # Add booking details to the patient's record
                        cost_details = calculate_appointment_cost(insurance_provider)
                        booking_details = {
                            "bookingId": str(uuid.uuid4()),
                            "bookingType": "appointment",
                            "doctorName": doctor_name,
                            "specialty": specialty,
                            "appointmentDate": appointment_date,
                            "appointmentTime": appointment_time,
                            "costBreakdown": cost_details,
                            "status": "confirmed",
                            "createdAt": firestore.SERVER_TIMESTAMP,
                            "updatedAt": firestore.SERVER_TIMESTAMP,
                            "doctorId": doctor_data.get('id'),
                            "location": doctor_data.get('city'),
                            "place": doctor_data.get('city')
                        }
                        current_bookings = patient_data.get('bookings', [])
                        current_bookings.append(booking_details)
                        transaction.update(patient_ref, {'bookings': current_bookings})
                        
                        return booking_details, patient_data.get('email')

                    # Find patient and doctor references
                    patients_ref = db.collection('patients')
                    patient_query = patients_ref.where(filter=firestore.FieldFilter('name', '==', patient_name)).limit(1).stream()
                    patient_doc_ref = next((doc.reference for doc in patient_query), None)
                    if not patient_doc_ref:
                        response_text = "I could not find a patient with the provided details. Please try again."
                    else:
                        doctors_ref = db.collection('doctors')
                        doctor_query = doctors_ref.where(filter=firestore.FieldFilter('name', '==', doctor_name)).limit(1).stream()
                        doctor_doc_ref = next((doc.reference for doc in doctor_query), None)
                        if not doctor_doc_ref:
                            response_text = "The doctor you selected could not be found."
                        else:
                            # Run the transaction
                            transaction = db.transaction()
                            try:
                                booking_details, patient_email = book_and_update_in_transaction(transaction, doctor_doc_ref, patient_doc_ref)
                                if patient_email:
                                    send_email_to_patient(patient_email, booking_details)
                                response_text = f"Success! Your booking with {doctor_name} has been confirmed. The total cost is ${booking_details['costBreakdown']['totalCost']:.2f} with a co-pay of ${booking_details['costBreakdown']['patientCopay']:.2f}. An email has been sent to your registered address."
                            except ValueError as ve:
                                response_text = f"I'm sorry, that time slot is no longer available. Please select a different time."
                            except Exception as e:
                                logging.error(f"Error during transaction: {e}")
                                logging.error(traceback.format_exc())
                                response_text = "I am having trouble processing your booking right now. Please try again later."

                else: # Mock data fallback
                    patient_data = MOCK_PATIENTS.get(patient_name)
                    doctor_data = MOCK_DOCTORS.get(doctor_name)
                    if patient_data and doctor_data and appointment_time in doctor_data['availability'].get(appointment_date, []):
                        cost_details = calculate_appointment_cost(insurance_provider)
                        booking_details = {
                            "bookingId": str(uuid.uuid4()),
                            "bookingType": "appointment",
                            "doctorName": doctor_name,
                            "specialty": specialty,
                            "appointmentDate": appointment_date,
                            "appointmentTime": appointment_time,
                            "costBreakdown": cost_details,
                            "status": "confirmed",
                            "doctorId": doctor_data.get('id'),
                            "location": doctor_data.get('city'),
                            "place": doctor_data.get('city')
                        }
                        
                        doctor_data['availability'][appointment_date].remove(appointment_time)
                        patient_data['bookings'].append(booking_details)
                        send_email_to_patient(patient_data['email'], booking_details)
                        response_text = f"Success! Your booking with {doctor_name} has been confirmed. An email has been sent to your registered address."
                    else:
                        response_text = "I could not complete the booking. Either the patient or doctor was not found, or the time slot is unavailable."
        
        except Exception as e:
            logging.error(f"Error during booking process: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble processing your booking right now. Please try again later."
    
    logging.info(f"Sending response to Dialogflow: {response_text}")
    return jsonify({
        'fulfillmentResponse': {
            'messages': [
                {
                    'text': {
                        'text': [response_text]
                    }
                }
            ]
        }
    })

# --- Application Entry Point ---
if __name__ == '__main__':
    logging.info("Starting application locally...")
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
