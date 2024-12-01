from aws_cdk import (
    Tags,
    Duration,
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_rds as rds,
    aws_elasticloadbalancingv2 as elb,
    aws_elasticloadbalancingv2_targets as tg,
    aws_certificatemanager as acm,
    aws_lambda as lambda_,
    aws_s3_notifications as s3n,
    aws_apigateway as apigateway,
    aws_ecs as ecs,
    aws_events as events,
    aws_events_targets as targets,
    aws_dynamodb as dynamodb,
)
from constructs import Construct
import os
import subprocess

class CdkPythonStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # account_id = account
        account_id = "012345678901"

        # VPC作成
        vpc_name = f"myproj-vpc-{account_id}"
        vpc = ec2.Vpc(
            self,
            vpc_name,
            vpc_name=vpc_name,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                # パブリックサブネット
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),

                # プライベートサブネット
                ec2.SubnetConfiguration(
                    name="Private1",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,     # パブリックサブネットにNATゲートウェイを配置
                    cidr_mask=24,
                ),
            ],
        )
        
        # # VPCエンドポイント用のセキュリティグループ
        # endpoint_sg = ec2.SecurityGroup(
        #     self,
        #     "EndpointSecurityGroup",
        #     vpc=vpc,
        #     description="Security group for VPC Endpoints",
        #     allow_all_outbound=False,  # アウトバウンドは制限する
        # )


        # # SSM用のVPCエンドポイントを作成
        # ssm_endpoint = vpc.add_interface_endpoint(
        #     "SSMEndpoint",
        #     service=ec2.InterfaceVpcEndpointService("com.amazonaws.ap-northeast-1.ssm", port=443),
        #     security_groups=[endpoint_sg],
        #     subnets=ec2.SubnetSelection(
        #         subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        #     ),
        #     open=False
        # )

        # RDS用のセキュリティグループを作成
        rds_sg = ec2.SecurityGroup(
            self,
            "RDSSecurityGroup",
            vpc=vpc,
            description="Security group for RDS instance",
            allow_all_outbound=False,
        )

        # RDSインスタンスを作成
        db_instance = rds.DatabaseInstance(
            self,
            "DevDatabase",
            # id="DevDatabase",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_3
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[rds_sg],
            multi_az=False,                      # シングルAZ構成
            deletion_protection=False,           # 削除保護無効
            # データベース設定
            database_name="devdb",
            credentials=rds.Credentials.from_generated_secret("postgres"),  # マスターユーザー名をpostgresに
            port=5432,
            # パラメータグループ
            parameters={
                "rds.force_ssl": "0",   # SSL接続の強制を無効化
            },
        )
        
        # EC2用のIAMロールを作成
        ec2_role = iam.Role(
            self,
            "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )

        # セッションマネージャーの使用に必要なマネージドポリシーを追加
        ec2_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
        )

        # EC2にシークレットマネージャーへのアクセスポリシーを追加
        ec2_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                resources=[db_instance.secret.secret_arn]  # このRDSインスタンスのシークレットのみにアクセス可能
            )
        )

        # EC2用のセキュリティグループを作成
        ec2_sg = ec2.SecurityGroup(
            self,
            "EC2SecurityGroup",
            vpc=vpc,
            description="Security group for EC2 instance",
            allow_all_outbound=True,  # アウトバウンドは全て許可
        )

        # EC2インスタンスを作成
        ec2_instance = ec2.Instance(
            self,
            "DevInstance",
            instance_name="DevInstance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MICRO
            ),
            machine_image=ec2.MachineImage.generic_linux({
                # Ubuntu 24.04 LTSのAMI IDを指定（AMIカタログのクイックスタートから確認）
                'ap-northeast-1': 'ami-0b20f552f63953f0e'
            }),
            security_group=ec2_sg,
            role=ec2_role,
            # ユーザーデータスクリプトを追加
            user_data=ec2.UserData.custom(f'''#!/bin/bash
# シークレットのARNを環境変数として設定
echo 'export DB_SECRET_ARN="{db_instance.secret.secret_arn}"' >> /etc/profile.d/db-secret.sh

# シークレットから環境変数を設定するスクリプトを作成
cat << 'EOF' > /usr/local/bin/set-db-env
#!/bin/bash
secret_json=$(aws secretsmanager get-secret-value --secret-id $DB_SECRET_ARN --query 'SecretString' --output text)
echo "DB_HOST=$(echo $secret_json | jq -r '.host')"
echo "DB_USER=$(echo $secret_json | jq -r '.username')"
echo "DB_PASSWORD=$(echo $secret_json | jq -r '.password')"
echo "DB_NAME=$(echo $secret_json | jq -r '.dbname')"
echo "DB_PORT=$(echo $secret_json | jq -r '.port')"
EOF

# スクリプトに実行権限を付与
chmod +x /usr/local/bin/set-db-env
''')
        )

        # ALB用のセキュリティグループを作成
        alb_sg = ec2.SecurityGroup(
            self,
            "ALBSecurityGroup",
            vpc=vpc,
            description="Security group for ALB",
            allow_all_outbound=False,
        )

        # SSL/TLS証明書の作成
        # 注: 事前にACMで証明書を作成しておく必要があります
        certificate = acm.Certificate.from_certificate_arn(
            self,
            "Certificate",
            # certificate_arn="arn:aws:acm:ap-northeast-1:012345678901:certificate/your-certificate-arn"  # 実際の証明書ARNに置き換えてください
            certificate_arn="arn:aws:acm:ap-northeast-1:637423399875:certificate/f5e47e39-1406-43a0-ab21-6cb270d79076"  # 実際の証明書ARNに置き換えてください
        )

        # ALB
        alb_instance = elb.ApplicationLoadBalancer(
            self,
            "alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            )
        )

        # HTTPSリスナーを追加
        https_listener = alb_instance.add_listener(
            "listener",
            port=443,
            certificates=[certificate],
            ssl_policy=elb.SslPolicy.RECOMMENDED,
            open=True
        )

        # ALBのターゲットグループを作成
        https_listener.add_targets(
            "target",
            port=80,
            targets=[tg.InstanceIdTarget(instance_id=ec2_instance.instance_id)],
            health_check=elb.HealthCheck(
                path="/",
            )
        )
        
        # HTTPからHTTPSへのリダイレクトを設定
        http_listener = alb_instance.add_listener(
            "HttpListener",
            port=80,
            open=True
        )
        http_listener.add_action(
            "HttpRedirect",
            action=elb.ListenerAction.redirect(
                protocol="HTTPS",
                port="443",
                permanent=True
            )
        )


        # # セキュリティグループ間の関連付け
        # # EC2からVPCエンドポイントへのアクセスを許可
        # endpoint_sg.add_ingress_rule(
        #     peer=ec2_sg,
        #     connection=ec2.Port.tcp(443),
        #     description="Allow HTTPS from EC2 instances"
        # )

        # # EC2からRDSへのアクセスを許可（PostgreSQLのポート5432）
        # rds_sg.add_ingress_rule(
        #     peer=ec2_sg,
        #     connection=ec2.Port.tcp(5432),
        #     description="Allow PostgreSQL access from EC2"
        # )

        # # セキュリティグループ間の関連付け
        # # EC2からALBへのアクセスを許可
        # alb_sg.add_ingress_rule(
        #     peer=ec2_sg,
        #     connection=ec2.Port.tcp(80),
        #     description="Allow HTTPS from EC2 instances"
        # )



        # S3バケット作成
        bucket_name = f"myproj-bucketname-{account_id}"
        bucket = s3.Bucket(
            self,
            bucket_name,
            bucket_name=bucket_name,                                # バケット名
            removal_policy=RemovalPolicy.DESTROY,                   # cdk destroy したときに削除
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL      # パブリックアクセス禁止
        )
        

        # SQLAlchemy, psycopg2用のLambdaレイヤーをDockerでビルド
        layer_path = "lambda-layers/db-layer"

        # Lambda Layerにパッケージをインストール
        subprocess.run(
            f"docker build {layer_path} -o {os.path.abspath(layer_path)}/python",
            shell=True,
            check=True
        )
        
        # Lambda Layerの作成
        db_layer = lambda_.LayerVersion(
            self,
            "DBLayer",
            code=lambda_.Code.from_asset(layer_path),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="SQLAlchemy and psycopg2 libraries for Lambda"
        )

        
        # Lambda用のセキュリティグループを作成
        lambda_sg = ec2.SecurityGroup(
            self,
            "LambdaSecurityGroup",
            vpc=vpc,
            description="Security group for Lambda",
            allow_all_outbound=True,
        )

        # Lambda関数の作成
        handler = lambda_.Function(
            self,
            "S3EventHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            code=lambda_.Code.from_asset("lambda/01_test"),
            handler="index.handler",
            timeout=Duration.seconds(10),
            environment={
                "BUCKET_NAME": bucket.bucket_name,
                "DB_SECRET_ARN": db_instance.secret.secret_arn,  # これを追加
            },
            layers=[db_layer],  # レイヤーを追加
            # VPC内にLambdaを配置する
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_sg]
        )

        # Lambda関数にSecrets Managerへのアクセス許可を付与
        get_secret_policy = iam.PolicyStatement(
            actions=[
                "secretsmanager:GetSecretValue"
            ],
            resources=[db_instance.secret.secret_arn]
        )
        handler.add_to_role_policy(get_secret_policy)

        # S3バケットにイベント通知を設定
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,  # オブジェクトが作成されたときのイベント
            s3n.LambdaDestination(handler),
            s3.NotificationKeyFilter(prefix="uploads/", suffix=".txt")  # 特定のプレフィックスとサフィックスを持つファイルのみを対象
        )


        # API Gateway + Lambda統合の設定
        api = apigateway.RestApi(
            self,
            "UsersApi",
            rest_api_name="Users API",
            description="API for managing users",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS
            )
        )

        # API用のLambda関数
        api_handler = lambda_.Function(
            self,
            "ApiHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            code=lambda_.Code.from_asset("lambda/02_api"),  # API Lambda関数のコードを配置するディレクトリ
            handler="index.handler",
            timeout=Duration.seconds(10),
            environment={
                "DB_SECRET_ARN": db_instance.secret.secret_arn,
            },
            layers=[db_layer],  # 既存のDBレイヤーを使用
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_sg]  # 既存のセキュリティグループを使用
        )

        # Secrets Managerへのアクセス権限を付与
        api_handler.add_to_role_policy(get_secret_policy)

        # APIリソースとメソッドの設定
        users = api.root.add_resource("users")
        user = users.add_resource("{id}")
        
        # /users エンドポイント
        users.add_method(
            "GET",
            apigateway.LambdaIntegration(api_handler)
        )
        users.add_method(
            "POST",
            apigateway.LambdaIntegration(api_handler)
        )
        
        # /users/{id} エンドポイント
        user.add_method(
            "GET",
            apigateway.LambdaIntegration(api_handler)
        )
        user.add_method(
            "PUT",
            apigateway.LambdaIntegration(api_handler)
        )
        user.add_method(
            "DELETE",
            apigateway.LambdaIntegration(api_handler)
        )





        # DynamoDBテーブルの作成（タスク実行状態の管理用）
        task_state_table = dynamodb.Table(
            self,
            "TaskStateTable",
            partition_key=dynamodb.Attribute(
                name="task_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl"  # TTLを設定して古いレコードを自動削除
        )



        # ECSクラスターの作成
        cluster = ecs.Cluster(
            self,
            "BatchCluster",
            vpc=vpc,
            cluster_name="batch-cluster"
        )

        # タスク定義の作成
        task_definition = ecs.FargateTaskDefinition(
            self,
            "BatchTaskDefinition",
            memory_limit_mib=512,
            cpu=256,
        )

        # コンテナの追加
        container = task_definition.add_container(
            "BatchContainer",
            image=ecs.ContainerImage.from_asset("ecs-tasks/batch-task"),  # Dockerfileのあるディレクトリを指定
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="batch-task"
            ),
            environment={
                "DYNAMODB_TABLE": task_state_table.table_name
            }
        )

        
        # タスク実行ロールにDynamoDBへのアクセス権限を追加
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:UpdateItem"
                ],
                resources=[task_state_table.table_arn]
            )
        )


        # EventBridgeルールの作成（1分おきに実行）
        rule = events.Rule(
            self,
            "ScheduleRule",
            schedule=events.Schedule.rate(Duration.minutes(1)),
        )

        # ECS用のセキュリティグループを作成
        ecs_sg = ec2.SecurityGroup(
            self,
            "ECSSecurityGroup",
            vpc=vpc,
            description="Security group for ECS tasks",
            allow_all_outbound=True,
        )

        # ECSタスクのターゲットを作成
        target = targets.EcsTask(
            cluster=cluster,
            task_definition=task_definition,
            subnet_selection=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[ecs_sg]
        )

        # ルールにターゲットを追加
        rule.add_target(target)


        # Lambda関数にS3バケットへのアクセス許可を付与
        bucket.grant_read(handler)
        bucket.grant_read_write(task_definition.task_role)
        
        # セキュリティグループ間の関連付け
        ec2_instance.connections.allow_from(alb_sg, ec2.Port.tcp(80), "Allow HTTP access from ALB to EC2")
        ec2_instance.connections.allow_to_any_ipv4(ec2.Port.tcp(443), "Allow HTTPS access from EC2 to any IP")
        db_instance.connections.allow_from(ec2_sg, ec2.Port.tcp(5432), "Allow PostgreSQL access from EC2 to RDS")
        db_instance.connections.allow_from(lambda_sg, ec2.Port.tcp(5432), "Allow PostgreSQL access from Lambda to RDS")
