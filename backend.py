from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from urllib.parse import quote_plus
import os
import uuid
import google.generativeai as genai
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText

load_dotenv()

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel('models/gemini-1.5-flash-002')

# Flask App Setup
app = Flask(__name__, template_folder='../frontend', static_folder='../frontend')
CORS(app)

# Serve React Frontend
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

# JWT Setup
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'fallback_secret_key')
bcrypt = Bcrypt(app)
jwt = JWTManager(app)

# MongoDB Setup
username = quote_plus(os.getenv("MONGODB_USERNAME"))
password = quote_plus(os.getenv("MONGODB_PASSWORD"))
dbname = os.getenv("MONGODB_DBNAME")
mongodb_uri = f"mongodb+srv://{username}:{password}@cluster0.omu1azd.mongodb.net/{dbname}?retryWrites=true&w=majority"
client = MongoClient(mongodb_uri)
db = client[dbname]
users_collection = db.users
reviews_collection = db.reviews
ratings_collection = db.ratings

# Upload Config
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# AUTH: Signup
@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    name, email, password = data.get("name"), data.get("email"), data.get("password")
    address = data.get("address")
    phone = data.get("phone")
    
    if not name or not email or not password:
        return jsonify({"error": "Missing required fields"}), 400
    if users_collection.find_one({"email": email}):
        return jsonify({"error": "Email already registered"}), 409

    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    users_collection.insert_one({
        "name": name, "email": email, "password": hashed_password,
        "address": address, "phone": phone
    })

    access_token = create_access_token(identity=email)
    return jsonify({"message": "Signup successful!", "token": access_token, "name": name}), 201

# AUTH: Login
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email, password = data.get("email"), data.get("password")
    if not email or not password:
        return jsonify({"error": "Missing email or password"}), 400
    user = users_collection.find_one({"email": email})
    if user and bcrypt.check_password_hash(user["password"], password):
        access_token = create_access_token(identity=user["email"])
        return jsonify({"token": access_token, "message": "Login successful!", "name": user["name"]}), 200
    return jsonify({"error": "Invalid credentials"}), 401

# AUTH: Protected route
@app.route('/protected', methods=['GET'])
@jwt_required()
def protected():
    return jsonify({"message": f"Hello, {get_jwt_identity()}"}), 200

# PHOTO UPLOAD
@app.route("/upload_photos", methods=["POST"])
def upload_photos():
    if "photos" not in request.files:
        return jsonify({"error": "No photos uploaded"}), 400
    files = request.files.getlist("photos")
    urls = []
    for file in files:
        filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(path)
        urls.append(f"/uploads/{filename}")
    return jsonify({"message": "Photos uploaded successfully!", "urls": urls}), 201

@app.route("/uploads/<filename>")
def get_uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ADD REVIEW
@app.route("/add_review", methods=["POST"])
def add_review():
    data = request.form
    photos = request.files.getlist("photos[]")
    image_urls = []
    for photo in photos:
        filename = str(uuid.uuid4()) + os.path.splitext(photo.filename)[1]
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        photo.save(path)
        image_urls.append(f"/uploads/{filename}")

    review = {
        "Name": data.get("Name"),
        "location": data.get("location"),
        "purpose": data.get("purpose"),
        "budget": data.get("budget"),
        "transport": data.get("transport"),
        "review": data.get("review"),
        "images": image_urls,
        "rating": 0,
        "rating_count": 0,
        "comments": []
    }
    reviews_collection.insert_one(review)
    return jsonify({"message": "Review added successfully!"}), 201

# GET REVIEWS
@app.route("/get_reviews", methods=["GET"])
def get_reviews():
    query = {}
    location = request.args.get("location")
    purpose = request.args.get("purpose")
    budget = request.args.get("budget")
    transport = request.args.get("transport")
    sort = request.args.get("sort")

    if location: query["location"] = location
    if purpose: query["purpose"] = purpose
    if budget: query["budget"] = budget
    if transport: query["transport"] = transport

    reviews_cursor = reviews_collection.find(query)
    if sort == "newest":
        reviews_cursor = reviews_cursor.sort("_id", -1)
    elif sort == "rating":
        reviews_cursor = reviews_cursor.sort("rating", -1)

    reviews = []
    for review in reviews_cursor:
        review["_id"] = str(review["_id"])
        reviews.append(review)

    return jsonify(reviews), 200

# ADD COMMENT
@app.route("/add_comment", methods=["POST"])
@jwt_required()
def add_comment():
    data = request.json
    review_id, comment = data.get("reviewId"), data.get("comment")
    if not review_id or not comment:
        return jsonify({"error": "Review ID and comment are required."}), 400
    user_email = get_jwt_identity()
    reviews_collection.update_one(
        {"_id": ObjectId(review_id)},
        {"$push": {"comments": {"user_email": user_email, "comment": comment}}}
    )
    return jsonify({"message": "Comment added successfully!"}), 200

# PROFILE
@app.route('/profile', methods=['GET'])
@jwt_required()
def profile():
    current_user = get_jwt_identity()
    user = users_collection.find_one({"email": current_user})
    if user:
        return jsonify({
            "name": user["name"],
            "email": user["email"],
            "address": user["address"],
            "phone": user["phone"]
        })
    return jsonify({"error": "User not found"}), 404

# UPDATE RATING
@app.route("/update_rating", methods=["POST"])
def update_rating():
    data = request.json
    review_id = data.get("reviewId")
    rating = data.get("rating")

    if not review_id or not rating:
        return jsonify({"error": "Review ID and rating are required."}), 400
    if not (1 <= rating <= 5):
        return jsonify({"error": "Rating must be between 1 and 5."}), 400

    review = reviews_collection.find_one({"_id": ObjectId(review_id)})
    if not review:
        return jsonify({"error": "Review not found."}), 404

    total_rating = review["rating"] * review["rating_count"] + rating
    new_rating_count = review["rating_count"] + 1
    new_avg_rating = total_rating / new_rating_count

    reviews_collection.update_one(
        {"_id": ObjectId(review_id)},
        {
            "$set": {
                "rating": new_avg_rating,
                "rating_count": new_rating_count
            }
        }
    )
    return jsonify({"message": "Rating updated successfully!"}), 200

# LIST GEMINI MODELS
@app.route("/list_models", methods=["GET"])
def list_models():
    try:
        models = list(genai.list_models())
        return jsonify([model.name for model in models]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Run the Flask app
if __name__ == "__main__":
    app.run(debug=True)
