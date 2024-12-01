import json
import logging
import boto3
import os
from typing import Dict, Any, List, Optional
from sqlalchemy import create_engine, text, Table, Column, Integer, String, MetaData, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from contextlib import contextmanager

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# SQLAlchemyの設定
Base = declarative_base()

# ユーザーモデルの定義
class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# データベース接続の管理
@contextmanager
def get_db_session():
    """データベースセッションを提供するコンテキストマネージャー"""
    engine = None
    session = None
    try:
        # シークレットの取得と接続URLの構築
        secrets = get_db_secret()
        db_url = f"postgresql://{secrets['username']}:{secrets['password']}@{secrets['host']}:{secrets['port']}/{secrets['dbname']}"
        
        # エンジンとセッションの作成
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # 初回実行時にテーブルを作成
        Base.metadata.create_all(engine)
        
        yield session
        session.commit()
    except Exception as e:
        if session:
            session.rollback()
        raise
    finally:
        if session:
            session.close()
        if engine:
            engine.dispose()

def get_db_secret() -> Dict[str, str]:
    """Secrets Managerからデータベース接続情報を取得"""
    secrets_client = boto3.client('secretsmanager')
    secret_arn = os.environ['DB_SECRET_ARN']
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    return json.loads(response['SecretString'])

def create_user(body: Dict[str, Any]) -> Dict[str, Any]:
    """新規ユーザーの作成"""
    with get_db_session() as session:
        user = User(
            name=body['name'],
            email=body['email']
        )
        session.add(user)
        session.commit()
        return {
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'created_at': user.created_at.isoformat(),
            'updated_at': user.updated_at.isoformat()
        }

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """ユーザー情報の取得"""
    with get_db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            return {
                'id': user.id,
                'name': user.name,
                'email': user.email,
                'created_at': user.created_at.isoformat(),
                'updated_at': user.updated_at.isoformat()
            }
        return None

def update_user(user_id: int, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """ユーザー情報の更新"""
    with get_db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.name = body.get('name', user.name)
            user.email = body.get('email', user.email)
            session.commit()
            return {
                'id': user.id,
                'name': user.name,
                'email': user.email,
                'created_at': user.created_at.isoformat(),
                'updated_at': user.updated_at.isoformat()
            }
        return None

def delete_user(user_id: int) -> bool:
    """ユーザーの削除"""
    with get_db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            session.delete(user)
            session.commit()
            return True
        return False

def list_users() -> List[Dict[str, Any]]:
    """全ユーザー情報の取得"""
    with get_db_session() as session:
        users = session.query(User).all()
        return [{
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'created_at': user.created_at.isoformat(),
            'updated_at': user.updated_at.isoformat()
        } for user in users]

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda関数のメインハンドラー"""
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        http_method = event['httpMethod']
        path = event['path']
        
        # パスパラメータの取得
        path_parameters = event.get('pathParameters', {})
        user_id = path_parameters.get('id') if path_parameters else None
        
        # リクエストボディの解析
        body = {}
        if event.get('body'):
            body = json.loads(event['body'])
        
        # エンドポイントの処理
        response_body = {}
        status_code = 200
        
        if path == '/users':
            if http_method == 'POST':
                response_body = create_user(body)
                status_code = 201
            elif http_method == 'GET':
                response_body = list_users()
        elif path == '/users/{id}':
            if not user_id:
                raise ValueError("User ID is required")
                
            if http_method == 'GET':
                user = get_user(int(user_id))
                if user:
                    response_body = user
                else:
                    status_code = 404
                    response_body = {"message": "User not found"}
            elif http_method == 'PUT':
                user = update_user(int(user_id), body)
                if user:
                    response_body = user
                else:
                    status_code = 404
                    response_body = {"message": "User not found"}
            elif http_method == 'DELETE':
                if delete_user(int(user_id)):
                    response_body = {"message": "User deleted successfully"}
                else:
                    status_code = 404
                    response_body = {"message": "User not found"}
        
        return {
            'statusCode': status_code,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
            },
            'body': json.dumps(response_body)
        }
        
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'message': 'Internal server error',
                'error': str(e)
            })
        }