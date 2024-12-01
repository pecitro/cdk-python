import json
import logging
import boto3
import os
from botocore.exceptions import ClientError
from typing import Dict, Any, List
from sqlalchemy import create_engine, text
from contextlib import contextmanager

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Secrets Managerクライアントの初期化
secrets_client = boto3.client('secretsmanager')

def get_db_secret() -> Dict[str, str]:
    """
    Secrets Managerからデータベース接続情報を取得する
    
    Returns:
        Dict[str, str]: データベース接続情報
    """
    try:
        # シークレット名はRDSインスタンス作成時に自動生成される
        secret_arn = os.environ.get('DB_SECRET_ARN')
        if not secret_arn:
            raise ValueError("DB_SECRET_ARN environment variable is not set")
            
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        secret = json.loads(response['SecretString'])
        return secret
    except ClientError as e:
        logger.error(f"Error retrieving database secret: {str(e)}")
        raise

@contextmanager
def get_db_connection():
    """
    データベース接続のコンテキストマネージャー
    """
    engine = None
    try:
        # データベース接続情報の取得
        db_secret = get_db_secret()
        
        # 接続URLの構築
        db_url = f"postgresql://{db_secret['username']}:{db_secret['password']}@{db_secret['host']}:{db_secret['port']}/{db_secret['dbname']}"
        
        # エンジンの作成
        engine = create_engine(db_url)
        
        # 接続のテスト
        with engine.connect() as connection:
            yield connection
            
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise
    finally:
        if engine:
            engine.dispose()

def process_file(bucket: str, key: str) -> None:
    """
    S3バケットから取得したファイルを処理し、データベースに接続する関数
    
    Args:
        bucket (str): S3バケット名
        key (str): オブジェクトのキー（ファイルパス）
    """
    try:
        # データベース接続のテスト
        with get_db_connection() as conn:
            # バージョン情報の取得
            result = conn.execute(text('SELECT version()'))
            version = result.scalar()
            logger.info(f"Successfully connected to PostgreSQL. Version: {version}")
            
            # テーブル一覧の取得
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """))
            tables = [row[0] for row in result]
            logger.info(f"Available tables: {tables}")
            
    except Exception as e:
        logger.error(f"Error processing file and connecting to database: {str(e)}")
        raise

def parse_s3_event(event: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    S3イベントを解析し、処理対象のファイル情報を抽出する
    
    Args:
        event (Dict[str, Any]): Lambda関数に渡されるイベントデータ
    
    Returns:
        List[Dict[str, str]]: バケット名とキーの組み合わせのリスト
    """
    files_to_process = []
    
    try:
        for record in event['Records']:
            if record['eventName'].startswith('ObjectCreated:'):
                files_to_process.append({
                    'bucket': record['s3']['bucket']['name'],
                    'key': record['s3']['object']['key']
                })
    except KeyError as e:
        logger.error(f"Error parsing S3 event. Missing key: {str(e)}")
        raise
    
    return files_to_process

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda関数のメインハンドラー
    
    Args:
        event (Dict[str, Any]): Lambda関数に渡されるイベントデータ
        context (Any): Lambda実行コンテキスト
    
    Returns:
        Dict[str, Any]: Lambda関数の実行結果
    """
    logger.info("Processing S3 event")
    logger.info(f"Event: {json.dumps(event)}")
    
    try:
        # イベントの解析
        files_to_process = parse_s3_event(event)
        logger.info(f"Found {len(files_to_process)} files to process")
        
        # 各ファイルの処理
        for file_info in files_to_process:
            bucket = file_info['bucket']
            key = file_info['key']
            
            logger.info(f"Processing file {key} from bucket {bucket}")
            process_file(bucket, key)
            
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Successfully processed S3 event and connected to database',
                'filesProcessed': len(files_to_process)
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing S3 event: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Error processing S3 event',
                'error': str(e)
            })
        }