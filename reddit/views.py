from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.template.defaulttags import register
from django.http import JsonResponse, HttpResponseBadRequest, Http404, HttpResponseForbidden
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from reddit.forms import UserForm, SubmissionForm
from django.contrib.auth.models import User
from reddit.models import RedditUser, Submission, Comment, Vote

@register.filter
def get_item(dictionary, key):
    """
    Needed because there's no built in .get in django templates
    when working with dictionaries.

    :param dictionary: python dictionary
    :param key: valid dictionary key type
    :return: value of that key or None
    """
    return dictionary.get(key)


def frontpage(request):
    """
    Serves frontpage and all additional submission listings
    with maximum of 25 submissions per page.
    """
    #TODO: Serve user votes on submissions too.

    all_submissions = Submission.objects.all()
    paginator = Paginator(all_submissions, 25)

    page = request.GET.get('page')
    try:
        submissions = paginator.page(page)
    except PageNotAnInteger:
        submissions = paginator.page(1)
    except EmptyPage:
        submissions = paginator.page(paginator.num_pages)

    return render(request, 'public/frontpage.html', {'submissions': submissions})


def comments(request, thread_id=None):
    """
    Handles comment view when user opens the thread.
    On top of serving all comments in the thread it will
    also return all votes user made in that thread
    so that we can easily update comments in template
    and display via css whether user voted or not.

    :param thread_id: Thread ID as it's stored in database
    :type thread_id: int
    """
    this_submission = Submission.objects.get(id=thread_id)
    if not this_submission:
        return Http404()

    thread_comments = Comment.objects.filter(submission=this_submission,
                                             parent=None).all()

    if request.user.is_authenticated():
        try:
            reddit_user = RedditUser.objects.get(user=request.user)
        except RedditUser.DoesNotExist:
            reddit_user = None
    else:
        reddit_user = None

    sub_vote_value = None
    try:
        vote = Vote.objects.get(vote_object_type=this_submission.get_content_type(),
                                vote_object_id=this_submission.id)
        sub_vote_value = vote.value
    except Vote.DoesNotExist:
        pass

    comment_votes = {}



    if reddit_user:
        try:
            user_thread_votes = Vote.objects.filter(user=reddit_user,
                                                    submission=this_submission)

            for vote in user_thread_votes:
                comment_votes[vote.vote_object.id] = vote.value
        except:
            pass

    return render(request, 'public/comments.html', {'submission': this_submission,
                                                    'comments': thread_comments,
                                                    'comment_votes': comment_votes,
                                                    'sub_vote': sub_vote_value})


def user_login(request):
    """
    Pretty straighforward user authentication using password and username
    supplied in the POST request.
    """

    if request.user.is_authenticated():
        messages.warning(request, "You are already logged in.")

    if request.method == "POST":
        user = authenticate(username=request.POST['username'],
                            password=request.POST['password'])

        if user:
            if user.is_active:
                login(request, user)
                redirect_url = request.POST.get('next') or 'reddit.views.frontpage'
                return redirect(redirect_url)
            else:
                return render(request, 'public/login.html',
                              {'login_error': "Account disabled"})
        else:
            return render(request, 'public/login.html',
                              {'login_error': "Wrong username or password."})

    return render(request, 'public/login.html')


def user_logout(request):
    """
    Log out user if one is logged in and redirect them to frontpage.
    """
    if not request.method == "POST":
        return HttpResponseBadRequest()

    if request.user.is_authenticated():
        logout(request)
        messages.success(request, 'Logged out!')
        return redirect('reddit.views.frontpage')
    return redirect('reddit.views.frontpage')


def register(request):
    """
    Handles user registration using UserForm from forms.py
    Creates new User and new RedditUser models if appropriate data
    has been supplied.

    If account has been created user is redirected to login page.
    """
    user_form = UserForm()

    if request.method == "POST":
        user_form = UserForm(request.POST)

        if user_form.is_valid():
            user = user_form.save()
            user.set_password(user.password)
            user.save()
            reddit_user = RedditUser()
            reddit_user.user = user
            reddit_user.save()
            messages.success(request, 'You have successfully registered! You can log in now')
            return render(request, 'public/login.html')

    return render(request, 'public/register.html', {'form': user_form})


def post_comment(request):
    if not request.user.is_authenticated():
        return JsonResponse({'msg': "You need to log in to post new comments."})

    parent_type = request.POST.get('parentType', None)
    parent_id = request.POST.get('parentId', None)
    raw_comment = request.POST.get('commentContent', None)

    if not all([parent_id, parent_type]) or \
                    parent_type not in ['comment', 'submission']:
        return HttpResponseBadRequest()

    if not raw_comment:
        return JsonResponse({'msg': "You have to write something."})

    author = RedditUser.objects.get(user=request.user)

    try:  # try and get comment or submission we're voting on
        if parent_type == 'comment':
            parent_object = Comment.objects.get(id=parent_id)
        elif parent_type == 'submission':
            parent_object = Submission.objects.get(id=parent_id)
        else:
            return HttpResponseBadRequest()

    except (Comment.DoesNotExist, Submission.DoesNotExist):
        return HttpResponseBadRequest()

    comment = Comment.create(author=author,
                             raw_comment=raw_comment,
                             parent=parent_object)

    comment.save()
    return JsonResponse({'msg': "Your comment has been posted."})


def vote(request):
    # The type of object we're voting on, can be 'submission' or 'comment'
    vote_object_type = request.POST.get('what', None)

    # The ID of that object as it's stored in the database, positive int
    vote_object_id = request.POST.get('what_id', None)

    # The value of the vote we're writing to that object, -1 or 1
    # Passing the same value twice will cancel the vote i.e. set it to 0
    new_vote_value = request.POST.get('vote_value', None)

    # By how much we'll change the score, used to modify score on the fly
    # client side by the javascript instead of waiting for a refresh.
    vote_diff = 0

    if not request.user.is_authenticated():
        return HttpResponseForbidden()
    else:
        user = RedditUser.objects.get(user=request.user)

    try:  # If the vote value isn't an integer that's equal to -1 or 1
        # the request is bad and we can not continue.
        new_vote_value = int(new_vote_value)

        if new_vote_value not in [-1, 1]:
            raise ValueError("Wrong value for the vote!")

    except ValueError:
        return HttpResponseBadRequest()

    # if one of the objects is None, 0 or some other bool(value) == False value
    # or if the object type isn't 'comment' or 'submission' it's a bad request
    if not all([vote_object_type, vote_object_id, new_vote_value]) or \
                    vote_object_type not in ['comment', 'submission']:
        return HttpResponseBadRequest()

    # Try and get the actual object we're voting on.
    try:
        if vote_object_type == "comment":
            vote_object = Comment.objects.get(id=vote_object_id)

        elif vote_object_type == "submission":
            vote_object = Submission.objects.get(id=vote_object_id)
        else:
            return HttpResponseBadRequest()  # should never happen

    except (Comment.DoesNotExist, Submission.DoesNotExist):
        return HttpResponseBadRequest

    # Try and get the existing vote for this object, if it exists.
    try:
        vote = Vote.objects.get(vote_object_type=vote_object.get_content_type(),
                                vote_object_id=vote_object.id,
                                user=user)

    except Vote.DoesNotExist:
        # Create a new vote and that's it.
        vote = Vote.create(user=user,
                           vote_object=vote_object,
                           vote_value=new_vote_value)
        vote.save()
        vote_diff = new_vote_value
        return JsonResponse({'error': None,
                             'voteDiff': vote_diff})

    # User already voted on this item, this means the vote is either
    # being canceled (same value) or changed (different new_vote_value)
    if vote.value == new_vote_value:
        # canceling vote
        if vote.value == 1:
            vote_diff = -1
            vote.vote_object.ups -= 1
            vote.vote_object.score -= 1
        elif vote.value == -1:
            vote_diff = 1
            vote.vote_object.downs -= 1
            vote.vote_object.score += 1

        vote.value = 0
        vote.vote_object.save()
        vote.save()
    else:
        # changing vote
        if vote.value == -1 and new_vote_value == 1:  # down to up
            vote_diff = 2
            vote.vote_object.score += 2
            vote.vote_object.ups += 1
            vote.vote_object.downs -= 1
        elif vote.value == 1 and new_vote_value == -1:  # up to down
            vote_diff = -2
            vote.vote_object.score -= 2
            vote.vote_object.ups -= 1
            vote.vote_object.downs += 1
        elif vote.value == 0 and new_vote_value == 1:  # canceled vote to up
            vote_diff = 1
            vote.vote_object.ups += 1
            vote.vote_object.score += 1
        elif vote.value == 0 and new_vote_value == -1:  # canceled vote to down
            vote_diff = -1
            vote.vote_object.downs += 1
            vote.vote_object.score -= 1
        else:
            return HttpResponseBadRequest('Wrong values for old/new vote combination')

        vote.value = new_vote_value
        vote.vote_object.save()
        vote.save()

    return JsonResponse({'error': None,
                         'voteDiff': vote_diff})


@login_required
def submit(request):
    """
    Handles new submission.. submission.
    """
    submission_form = SubmissionForm()

    if request.method == 'POST':
        submission_form = SubmissionForm(request.POST)
        if submission_form.is_valid():
            submission = submission_form.save(commit=False)
            user = User.objects.get(username=request.user)
            redditUser = RedditUser.objects.get(user=user)
            submission.author = redditUser
            submission.save()
            messages.success(request, 'Submission created')
            return redirect('/comments/{}'.format(submission.id))

    return render(request, 'public/submit.html', {'form': submission_form})


@login_required
def test_data(request):
    """
    Quick and dirty way to create 10 random submissions random comments each
    and up to 100 users with usernames (their passwords are same as usernames)

    Should be removed in production.

    """
    if not request.user.is_staff:
        return HttpResponseForbidden("There's nothing to see here.")

    thread_count = request.GET.get('threads', 10)
    root_comments = request.GET.get('comments', 10)

    from random import choice, randint
    from string import letters

    def get_random_username(length=6):
        return ''.join(choice(letters) for i in range(length))

    random_usernames = [get_random_username() for _ in range(100)]
    print random_usernames

    def get_random_sentence(min_words=3, max_words=50,
                            min_word_len=3,
                            max_word_len=15):
        sentence = ''

        for _ in range(0, randint(min_words, max_words)):
            sentence += ''.join(choice(letters)
                                for i in range(randint(min_word_len, max_word_len)))
            sentence += ' '

        return sentence

    def get_or_create_author(username):
        try:
            user = User.objects.get(username=username)
            author = RedditUser.objects.get(user=user)
        except (User.DoesNotExist, RedditUser.DoesNotExist):
            print "Creating user {}".format(username)
            new_author = User(username=username)
            new_author.set_password(username)
            new_author.save()
            author = RedditUser(user=new_author)
            author.save()
        return author

    def add_replies(root_comment, depth=1):
        print "Adding comment replies..."
        if depth > 5:
            return

        comment_author = get_or_create_author(choice(random_usernames))

        raw_text = get_random_sentence()
        new_comment = Comment.create(comment_author, raw_text, root_comment)
        new_comment.save()
        if choice([True, False]):
            add_replies(new_comment, depth + 1)

    for _ in range(thread_count):
        print "Creating new submission."
        selftext = get_random_sentence()
        title = get_random_sentence(max_words=10, max_word_len=10)
        author = get_or_create_author(choice(random_usernames))
        ups = randint(0, 1000)
        url = None
        downs = int(ups) / 2
        comments = 0

        submission = Submission(author=author,
                                title=title,
                                url=url,
                                text=selftext,
                                ups=int(ups),
                                downs=downs,
                                score=ups - downs,
                                comment_count=comments)
        submission.save()

        for _ in range(root_comments):
            comment_author = get_or_create_author(choice(random_usernames))
            raw_text = get_random_sentence(max_words=100)
            new_comment = Comment.create(comment_author, raw_text, submission)
            new_comment.save()
            another_child = choice([True, False])
            while another_child:
                add_replies(new_comment)
                another_child = choice([True, False])

    return redirect('/')