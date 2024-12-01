#!/bin/sh

TASK_ID="batch-task-1"
REGION="ap-northeast-1"

# DynamoDBにロックを取得する関数
acquire_lock() {
    ttl=$(($(date +%s) + 1800))  # 30分後のTTL
    
    echo "Attempting to acquire lock for task $TASK_ID"
    echo "Using DynamoDB table: $DYNAMODB_TABLE"
    
    # エラー出力を変数に保存
    error_output=$(aws dynamodb put-item \
        --table-name $DYNAMODB_TABLE \
        --region $REGION \
        --item "{\"task_id\":{\"S\":\"$TASK_ID\"},\"status\":{\"S\":\"running\"},\"ttl\":{\"N\":\"$ttl\"}}" \
        --condition-expression "attribute_not_exists(task_id)" \
        2>&1)
    
    # 終了コードを保存
    result=$?
    
    # エラーがあれば出力
    if [ $result -ne 0 ]; then
        echo "Error acquiring lock: $error_output"
    else
        echo "Lock acquired successfully"
    fi
    
    return $result
}

# ロックを解放する関数
release_lock() {
    echo "Releasing lock for task $TASK_ID"
    error_output=$(aws dynamodb delete-item \
        --table-name $DYNAMODB_TABLE \
        --region $REGION \
        --key "{\"task_id\":{\"S\":\"$TASK_ID\"}}" \
        2>&1)
        
    if [ $? -ne 0 ]; then
        echo "Error releasing lock: $error_output"
    else
        echo "Lock released successfully"
    fi
}

# ロックの取得を試みる
if ! acquire_lock; then
    echo "Task is already running or error occurred. Exiting."
    exit 0
fi

# 終了時にロックを解放するようにトラップを設定
trap release_lock EXIT

echo "Task started at $(date)"

# テスト用に120秒待機
sleep 120

echo "Task completed at $(date)"