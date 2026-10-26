# Teardown and clean check

```bash
terraform -chdir=infra destroy -no-color 2>&1 | tee build/results/tf-destroy.txt
```

**`Destroy complete! Resources: 43 destroyed.`** That's the base 34 resources, plus the NLB's 7 and the autoscaling 2 that were still on. The Cloud Map namespace took 38 s (screenshot [`27`](screenshots/27-destroy-43.png)).

## Clean check

All queries are single-quoted, because of the backticks (screenshot [`28`](screenshots/28-clean-check.png)):

```bash
terraform -chdir=infra state list | wc -l
aws ecs describe-clusters --clusters mvelaphanda-routes --query 'clusters[?status==`ACTIVE`].clusterName' --output text
aws ecs list-task-definitions --family-prefix mvelaphanda-routes --status ACTIVE --query 'taskDefinitionArns' --output text
aws ecs describe-capacity-providers --capacity-providers mvelaphanda-routes-ec2 --query 'capacityProviders[?status==`ACTIVE`].name' --output text
aws autoscaling describe-auto-scaling-groups --query 'AutoScalingGroups[?starts_with(AutoScalingGroupName, `mvelaphanda-routes`)].AutoScalingGroupName' --output text
aws ec2 describe-launch-templates --query 'LaunchTemplates[?starts_with(LaunchTemplateName, `mvelaphanda-routes`)].LaunchTemplateName' --output text
aws ec2 describe-instances --filters Name=tag:Project,Values=aws-ecs-capacity-architecture Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text
aws elbv2 describe-load-balancers --query 'LoadBalancers[?starts_with(LoadBalancerName, `mvelaphanda-routes`)].LoadBalancerName' --output text
aws elbv2 describe-target-groups --query 'TargetGroups[?starts_with(TargetGroupName, `mvelaphanda-routes`)].TargetGroupName' --output text
aws application-autoscaling describe-scalable-targets --service-namespace ecs --query 'ScalableTargets[?contains(ResourceId, `mvelaphanda-routes`)].ResourceId' --output text
aws servicediscovery list-namespaces --query 'Namespaces[?Name==`mvelaphanda-routes`].Id' --output text
aws cloudwatch describe-alarms --query 'MetricAlarms[?contains(AlarmName, `mvelaphanda-routes`)].AlarmName' --output text
aws logs describe-log-groups --log-group-name-prefix /ecs/mvelaphanda-routes --query 'logGroups[].logGroupName' --output text
aws iam list-roles --query 'Roles[?starts_with(RoleName, `mvelaphanda-routes`)].RoleName' --output text
aws iam list-instance-profiles --query 'InstanceProfiles[?starts_with(InstanceProfileName, `mvelaphanda-routes`)].InstanceProfileName' --output text
aws ec2 describe-security-groups --filters Name=tag:Project,Values=aws-ecs-capacity-architecture --query 'SecurityGroups[].GroupId' --output text
aws ec2 describe-vpcs --filters Name=tag:Project,Values=aws-ecs-capacity-architecture --query 'Vpcs[].VpcId' --output text
aws s3 ls "s3://$STATE_BUCKET/aws-ecs-capacity-architecture/"
```

**Result:**
- the state list was `0`;
- every check printed nothing;
- the state key holds only `terraform.tfstate` at **571 bytes**, an empty state.

**Two things outside Terraform also needed checking:**
- **The Auto Scaling group** that I force-deleted during the [EC2 incident](debugging-journey.md). Its instances were already gone before the final destroy: the check of instances with the project tag was empty after the recovery (screenshot [`20`](screenshots/20-after-ec2-removal-check.png)).
- **The `INACTIVE` `eta-batch` service and the deregistered task definitions.** ECS keeps these as records; they cost nothing and can't be deleted.
